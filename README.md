# Welcome to your Lovable project

This project was built with [Lovable](https://lovable.dev).

## Build with Lovable

Open your project in the [Lovable editor](https://lovable.dev) and keep building.

- **Ship faster**: describe what you want to build and Lovable handles the code.
- **Stay in sync**: connect the project to GitHub and every change made in Lovable is committed straight to your repository.
- **Full ownership**: this code is yours. Push to your repository and your changes sync back into Lovable, ready for your next prompt.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```

## Built with

- TanStack Start
- TypeScript
- React
- Tailwind CSS

## RoboGuide AI — backend integration

The frontend talks to the backend only through `src/services/roboguide-api.ts`.

- **Configure** with Vite env vars (see `.env.example`): `VITE_ROBOGUIDE_API_URL`, `VITE_ROBOGUIDE_ASK_PATH`, timeout and mode values.
- **Demo mode** runs automatically when `VITE_ROBOGUIDE_API_URL` is empty (or `VITE_ROBOGUIDE_MOCK=true`). Mock answers live in `src/services/mock-roboguide.ts`, are flagged `isDemo`, and are labelled "Demo" in the UI. Add `#error` or `#notfound` to a question to preview those states.
- **Request** (`buildRequestBody`): `{ question, robot, mode, response_language, conversation_id }`.
- **Response** (`mapResponse`): accepts `answer` + `sources[{ manual, page, excerpt, url, language }]` and common aliases. Missing pages/links stay empty; an empty answer or `found: false` shows the "couldn't find enough information" message.
- **Machines, FAQs, categories, languages**: `src/config/app.ts` (sample data, easy to replace with backend data).
- **Chat history**: browser localStorage via `src/lib/chat-store.ts`; swap its read/write functions for backend storage later.
