# PDF MCP 서버 (회사 챗봇 연결용)

첨부된 마크다운(.md) 원문을 PDF로 변환해주는 원격 MCP 서버입니다.
회사 AI 챗봇의 "extension 연결" 기능이 **URL 입력 방식(HTTP)** 이라고 하셔서,
그에 맞춰 Streamable HTTP 트랜스포트로 만들어졌습니다.

## 동작 방식

1. 사용자가 챗봇에 마크다운 글을 첨부하고 "PDF로 만들어줘"라고 요청
2. 챗봇이 이 서버의 `create_pdf` 도구를 호출 (제목 + 마크다운 원문 그대로 전달)
3. 서버가 PDF를 생성해서:
   - 15MB 이하면 **파일 자체를 base64로 인코딩해서 응답에 첨부** (클라이언트가 지원하는 경우 바로 다운로드/표시됨)
   - 항상 **다운로드 링크**(`/files/파일명.pdf`)도 함께 반환 (파일 첨부를 지원하지 않는 클라이언트를 위한 폴백)
4. 사용자는 챗봇 화면에서 파일을 바로 받거나, 안내된 링크를 클릭해서 받음

> 이 서버는 챗봇과 완전히 분리된 원격 프로세스(HTTP로만 연결)이므로, 서버 쪽에만
> 존재하는 로컬 경로(`/mnt/outputs` 등)에 파일을 저장해도 챗봇이 그 파일을 읽어갈 수
> 없습니다. 두 서비스가 같은 볼륨을 실제로 공유 마운트하고 있는 게 아니라면, 파일
> 전달은 항상 위의 다운로드 링크/base64 첨부 방식으로만 이뤄집니다.

> 파일 첨부가 실제로 "다운로드 가능한 파일"처럼 보일지는 회사 챗봇 클라이언트가
> MCP의 `resource` content 타입을 어떻게 렌더링하는지에 달려있습니다. 한번 테스트해보시고,
> 만약 파일 첨부가 텍스트로 깨져 보인다거나 인식이 안 되면 링크 방식만 쓰도록 안내드릴게요.

## 설치 및 로컬 실행

```bash
npm install
npm run build
BASE_URL=http://localhost:3000 PORT=3000 npm start
```

정상 기동되면 `http://localhost:3000/health` 로 상태 확인 가능합니다.

## Railway로 간단히 배포해보기 (처음 호스팅 해보시는 경우 추천)

Dockerfile이 이미 준비되어 있어서, Railway는 자동으로 이 Dockerfile을 인식해 빌드해줍니다.

1. **GitHub에 코드 올리기**
   - 이 폴더로 새 저장소를 만들고 push합니다. (`node_modules`, `build`, `generated-pdfs`는
     `.gitignore`에 이미 포함되어 있어 올라가지 않습니다.)
   ```bash
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin <본인의 GitHub 저장소 URL>
   git push -u origin main
   ```

2. **Railway 가입 및 프로젝트 생성**
   - [railway.app](https://railway.app) 에 GitHub 계정으로 가입/로그인
   - "New Project" → "Deploy from GitHub repo" → 방금 만든 저장소 선택
   - Railway가 `Dockerfile`을 자동으로 감지해서 빌드를 시작합니다 (첫 빌드는 Chromium
     설치 때문에 몇 분 걸릴 수 있습니다).

3. **공개 도메인 생성**
   - 배포된 서비스의 "Settings" → "Networking" → "Generate Domain" 클릭
   - `https://your-app.up.railway.app` 같은 주소가 생성됩니다.

4. **환경변수 설정** (서비스의 "Variables" 탭)
   - `BASE_URL` = 3번에서 생성된 도메인 (예: `https://your-app.up.railway.app`)
     — 다운로드 링크 생성에 쓰이므로 반드시 실제 접근 가능한 주소로 넣어야 합니다.
   - `MCP_AUTH_TOKEN` = 아무 문자열이나 정해서 넣기 (예: openssl로 생성한 랜덤 값).
     테스트만 할 거면 생략해도 되지만, 잠깐이라도 켜두면 아무나 호출할 수 있으니
     설정을 권장합니다.
   - 변수를 저장하면 자동으로 재배포됩니다.

5. **정상 동작 확인**
   ```bash
   curl https://your-app.up.railway.app/health
   # {"status":"ok"} 가 나오면 정상
   ```

6. **회사 챗봇에 연결**
   - extension/커넥터 URL 입력란에: `https://your-app.up.railway.app/mcp`
   - `MCP_AUTH_TOKEN`을 설정했다면 챗봇이 커스텀 헤더를 지원하는지 확인하고
     `Authorization: Bearer <토큰>`을 함께 설정하세요. 지원하지 않으면 테스트 단계에서는
     토큰 없이 진행하고, 실사용 전에 다시 보안을 검토하세요.

### Render를 쓰고 싶다면

절차는 거의 동일합니다: render.com 가입 → "New Web Service" → GitHub 저장소 연결 →
Render가 `Dockerfile`을 자동 인식 → 배포 후 제공되는 `https://xxxx.onrender.com` 주소를
`BASE_URL`로 설정 → 환경변수(`MCP_AUTH_TOKEN` 등) 추가.

> **참고**: Railway/Render 무료(또는 최저) 플랜은 일정 시간 요청이 없으면 서버가
> 잠들었다가(sleep) 첫 요청 때 다시 깨어나는 경우가 있습니다. 테스트 단계에서는
> 문제없지만, 실사용 단계로 넘어가면 유료 플랜(항상 켜져 있는 인스턴스)을 고려하세요.

## 회사 챗봇에 연결하기

Railway(또는 Render)에서 받은 도메인에 `/mcp`를 붙여서 챗봇의 extension/커넥터
URL 입력란에 넣으면 됩니다. 예: `https://your-app.up.railway.app/mcp`

`MCP_AUTH_TOKEN`을 설정했다면, 챗봇 쪽 설정에서 인증 헤더
(`Authorization: Bearer <토큰>`)를 함께 입력할 수 있는지 확인하세요. 챗봇이 커스텀
헤더 설정을 지원하지 않으면, 테스트 단계에서는 토큰 없이 진행하고 실사용 전에
다시 보안을 검토하세요.

## 커스터마이징

- **PDF 스타일 변경**: `src/index.ts`의 `css` 부분 수정 (폰트, 여백, 색상 등)
- **생성 파일 정리**: `generated-pdfs/` 폴더에 파일이 계속 쌓이므로, 운영 시에는
  주기적으로 오래된 파일을 지우는 배치(cron)를 추가하는 걸 권장합니다.
- **인라인 첨부 크기 제한**: `MAX_INLINE_BYTES` 값을 조정해 파일을 직접 첨부할
  최대 크기를 바꿀 수 있습니다. (기본 15MB, 넘으면 링크만 반환)

## 보안 관련 참고

- 사내 전용이라도 `MCP_AUTH_TOKEN`으로 최소한의 인증을 걸어두는 걸 권장합니다.
- HTTPS 뒤에 배포하세요 (사내 리버스 프록시 등).
- `/files/:fileName` 라우트는 경로 탈출(`../` 등)을 막도록 `path.basename`으로
  파일명을 정리하지만, 그래도 외부에 노출되는 엔드포인트인 만큼 내부망에서만
  접근되도록 네트워크 단에서도 막아두는 게 안전합니다.
