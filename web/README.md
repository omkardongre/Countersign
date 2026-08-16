# Countersign case desk

GMP operator UI for deviation review. Not a chatbot.

```bash
cd web
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

- Queue, case, and open-intake routes
- Labelled synthetic fixtures (`AX-2291` not_ready, `AX-2402` ready for QP, `HX-104` sterility hold)
- Deterministic severity on intake (same rules as `app/domain/classification.py`)
- QP confirm refused while the Annex 16 pack is `not_ready`
- Live Runtime proxy is **not** implemented; `POST /api/cases` with `source: live` returns 501 unless/until `COUNTERSIGN_CASE_ENGINE_URL` is wired

Intake-opened cases live in this Node process and reset on restart.
