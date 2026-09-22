# Trading Copilot Web

Read-only Next.js workstation for the FastAPI trading copilot.

```bash
cp .env.example .env.local
npm install
npm run dev
```

Set `NEXT_PUBLIC_API_BASE_URL` to the FastAPI origin. The browser uses the Next.js `/backend`
proxy, so it never connects to Bybit or receives exchange credentials.
