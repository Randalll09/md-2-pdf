#!/usr/bin/env node
import express, { type Request, type Response } from "express";
import { randomUUID } from "node:crypto";
import path from "node:path";
import fs from "node:fs/promises";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";
import { mdToPdf } from "md-to-pdf";
import { z } from "zod";

// ── 설정 ──────────────────────────────────────────────────────────────
// 이 서버가 외부에서 접근되는 주소. 챗봇이 반환된 다운로드 링크를 클릭할 수
// 있도록, 실제 배포 주소(사내 도메인/IP)로 반드시 바꿔주세요.
const BASE_URL = process.env.BASE_URL || "http://localhost:3000";
const PORT = Number(process.env.PORT || 3000);
// 사내망 전용이라도 아무나 호출 못 하게 최소한의 토큰 인증을 겁니다.
// 값을 비워두면(미설정) 인증 없이 열립니다 — 운영 환경에서는 꼭 설정하세요.
const AUTH_TOKEN = process.env.MCP_AUTH_TOKEN || "";
const FILES_DIR = path.join(process.cwd(), "generated-pdfs");

function sanitizeFileName(name: string): string {
  return (
    name
      .replace(/[\\/:*?"<>|]/g, "")
      .trim()
      .slice(0, 80) || "document"
  );
}

// ── MCP 서버 정의 ─────────────────────────────────────────────────────
function buildMcpServer() {
  const server = new McpServer({ name: "pdf-mcp-server", version: "2.0.0" });

  server.registerTool(
    "create_pdf",
    {
      title: "create_pdf",
      description:
        "사용자가 첨부한 마크다운(.md) 원문을 그대로 PDF로 변환합니다. " +
        "내용을 요약하거나 재구성하지 말고 원문 그대로 content에 담아 호출하세요. " +
        "결과로 다운로드 가능한 URL을 반환하니, 그 링크를 사용자에게 그대로 안내해주세요.",
      inputSchema: {
        title: z
          .string()
          .describe(
            "파일명으로 사용할 제목. content가 이미 '#' 제목으로 시작하면 본문에는 중복 추가되지 않습니다."
          ),
        content: z
          .string()
          .describe(
            "첨부된 마크다운 원문 그대로. 요약/재작성 금지. #, -, **bold**, 코드블록 등 마크다운 문법을 그대로 전달하세요."
          ),
      },
    },
    async ({ title, content }) => {
      await fs.mkdir(FILES_DIR, { recursive: true });

      const alreadyHasHeading = /^\s*#{1,6}\s+/.test(content);
      const markdown = alreadyHasHeading ? content : `# ${title}\n\n${content}`;

      const uniqueId = randomUUID().slice(0, 8);
      const fileName = `${sanitizeFileName(title)}-${uniqueId}.pdf`;
      const destPath = path.join(FILES_DIR, fileName);

      const pdf = await mdToPdf(
        { content: markdown },
        {
          dest: destPath,
          pdf_options: {
            format: "A4",
            margin: { top: "20mm", bottom: "20mm", left: "18mm", right: "18mm" },
          },
          launch_options: process.env.PUPPETEER_EXECUTABLE_PATH
            ? { executablePath: process.env.PUPPETEER_EXECUTABLE_PATH, args: ["--no-sandbox"] }
            : undefined,
          css: `
            body { font-family: -apple-system, "Malgun Gothic", "Apple SD Gothic Neo", sans-serif; line-height: 1.6; }
            h1 { border-bottom: 2px solid #333; padding-bottom: 8px; }
          `,
        }
      );

      if (!pdf || !pdf.filename) {
        throw new Error("PDF 생성에 실패했습니다.");
      }

      const downloadUrl = `${BASE_URL}/files/${encodeURIComponent(fileName)}`;

      // 클라이언트가 embedded resource(파일 첨부)를 지원하면 파일 자체를 바로 받고,
      // 지원하지 않으면 링크로 폴백할 수 있도록 두 가지를 함께 반환합니다.
      const fileBuffer = await fs.readFile(destPath);
      const MAX_INLINE_BYTES = 15 * 1024 * 1024; // 15MB - 너무 크면 링크만 반환
      const canInline = fileBuffer.byteLength <= MAX_INLINE_BYTES;

      return {
        content: [
          {
            type: "text" as const,
            text: canInline
              ? `PDF가 생성되었습니다. (파일이 첨부되지 않으면 이 링크로 다운로드하세요: ${downloadUrl})`
              : `PDF가 생성되었습니다. 파일이 커서(${(fileBuffer.byteLength / 1024 / 1024).toFixed(1)}MB) 직접 첨부하지 않고 링크로 안내합니다:\n${downloadUrl}`,
          },
          ...(canInline
            ? [
                {
                  type: "resource" as const,
                  resource: {
                    uri: downloadUrl,
                    mimeType: "application/pdf",
                    blob: fileBuffer.toString("base64"),
                  },
                },
              ]
            : []),
        ],
      };
    }
  );

  return server;
}

// ── Express + Streamable HTTP 트랜스포트 ────────────────────────────
// 세션(mcp-session-id)마다 별도의 McpServer/transport 인스턴스를 만들어
// 서로 다른 사용자의 요청/응답이 섞이지 않도록 합니다.
const app = express();
app.use(express.json());

if (AUTH_TOKEN) {
  app.use((req, res, next) => {
    if (req.path === "/health") return next();
    const auth = req.header("authorization") || "";
    if (auth === `Bearer ${AUTH_TOKEN}`) return next();
    res.status(401).json({ error: "unauthorized" });
  });
}

type SessionEntry = { transport: StreamableHTTPServerTransport };
const sessions = new Map<string, SessionEntry>();

app.post("/mcp", async (req: Request, res: Response) => {
  const sessionId = req.header("mcp-session-id");
  let entry = sessionId ? sessions.get(sessionId) : undefined;

  if (!entry && isInitializeRequest(req.body)) {
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: () => randomUUID(),
      onsessioninitialized: (sid) => {
        sessions.set(sid, { transport });
      },
    });
    transport.onclose = () => {
      if (transport.sessionId) sessions.delete(transport.sessionId);
    };
    const server = buildMcpServer();
    await server.connect(transport);
    entry = { transport };
    await transport.handleRequest(req, res, req.body);
    return;
  }

  if (!entry) {
    res.status(400).json({ error: "유효하지 않은 세션입니다. 먼저 initialize 요청을 보내세요." });
    return;
  }

  await entry.transport.handleRequest(req, res, req.body);
});

app.get("/mcp", async (req: Request, res: Response) => {
  const sessionId = req.header("mcp-session-id");
  const entry = sessionId ? sessions.get(sessionId) : undefined;
  if (!entry) {
    res.status(400).send("유효하지 않은 세션입니다.");
    return;
  }
  await entry.transport.handleRequest(req, res);
});

app.delete("/mcp", async (req: Request, res: Response) => {
  const sessionId = req.header("mcp-session-id");
  const entry = sessionId ? sessions.get(sessionId) : undefined;
  if (!entry) {
    res.status(400).send("유효하지 않은 세션입니다.");
    return;
  }
  await entry.transport.handleRequest(req, res);
});

// 생성된 PDF 다운로드용 정적 라우트
app.get("/files/:fileName", async (req: Request, res: Response) => {
  const fileName = path.basename(req.params.fileName); // 경로 탈출 방지
  const filePath = path.join(FILES_DIR, fileName);
  try {
    await fs.access(filePath);
  } catch {
    res.status(404).send("파일을 찾을 수 없습니다.");
    return;
  }
  res.download(filePath);
});

app.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok" });
});

app.listen(PORT, () => {
  console.log(`PDF MCP 서버가 http://localhost:${PORT}/mcp 에서 실행 중입니다.`);
  console.log(`외부 공개 주소(BASE_URL): ${BASE_URL}`);
  if (!AUTH_TOKEN) {
    console.warn(
      "경고: MCP_AUTH_TOKEN이 설정되지 않았습니다. 운영 환경에서는 반드시 설정하세요."
    );
  }
});
