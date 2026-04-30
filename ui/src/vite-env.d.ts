/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_TEMPORAL_UI_URL?: string
  readonly VITE_MAILHOG_UI_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
