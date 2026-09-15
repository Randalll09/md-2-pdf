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
        "Converts the user's attached markdown (.md) source into a PDF, unchanged. " +
        "Do not summarize or rewrite the content — pass the original text as-is in `content`. " +
        "`title` is used as the file name. If the user didn't give a file name and `content` " +
        "has no '#' heading either, don't leave title empty — come up with a short, suitable " +
        "title yourself based on the content and pass it. " +
        "The result includes a downloadable URL; pass that link along to the user as-is.",
      inputSchema: {
        title: z
          .string()
          .optional()
          .describe(
            "Title to use as the file name. If `content` already starts with a '#' heading, " +
              "it won't be duplicated in the body. If the user didn't specify a file name, " +
              "generate a suitable title from the content and fill it in."
          ),
        content: z
          .string()
          .describe(
            "The attached markdown source, verbatim. Do not summarize or rewrite it. " +
              "Pass markdown syntax (#, -, **bold**, code blocks, etc.) through unchanged."
          ),
      },
    },
    async ({ title, content }) => {
      await fs.mkdir(FILES_DIR, { recursive: true });

      const headingMatch = content.match(/^\s*#{1,6}\s+(.+)$/m);
      const alreadyHasHeading = Boolean(headingMatch);
      // 챗봇이 title을 채워 호출하는 게 정상 경로지만, 혹시 비어 오더라도 본문 제목
      // 또는 날짜 기반 이름으로 대체해 실패 없이 진행합니다.
      const resolvedTitle =
        title?.trim() || headingMatch?.[1]?.trim() || `문서-${new Date().toISOString().slice(0, 10)}`;
      const markdown = alreadyHasHeading ? content : `# ${resolvedTitle}\n\n${content}`;

      const uniqueId = randomUUID().slice(0, 8);
      const fileName = `${sanitizeFileName(resolvedTitle)}-${uniqueId}.pdf`;
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
