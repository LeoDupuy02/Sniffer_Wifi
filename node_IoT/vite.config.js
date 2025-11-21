import { defineConfig } from "vite";

export default defineConfig({
  server: {
    host: true,
    allowedHosts: [
      'unrenovated-dishonorably-patience.ngrok-free.dev'
    ],
    cors: true,
  },
});
