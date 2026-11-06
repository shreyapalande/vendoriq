# VendorIQ frontend

React + JavaScript + Vite + Tailwind CSS v4.

In development, the Vite dev server proxies `/api` to the FastAPI backend at
`http://127.0.0.1:8000` (see `vite.config.js`), so the app and the API share one
origin in the browser.

```
npm install      # once
npm run dev      # http://localhost:5173 (start the backend too)
npm run lint
npm run build
```

The frontend only hides or shows things; every permission check happens in the backend.
