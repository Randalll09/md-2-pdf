# Chromium(md-to-pdf 내부에서 사용) 실행에 필요한 라이브러리와 한글 폰트를 포함한 이미지
FROM node:20-bookworm-slim

# 시스템 Chromium + 한글(CJK) 폰트 설치, puppeteer 자체 다운로드는 생략
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    NODE_ENV=production

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]
