import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../ticketbunker/app_assets",
    emptyOutDir: true,
  },
  clearScreen: false,
  server: {
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test-setup.ts",
  },
});
