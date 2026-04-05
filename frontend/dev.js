import { createServer } from "vite";
import { fileURLToPath } from "url";
import { dirname } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));

const server = await createServer({
  root: __dirname,
  server: { port: 5173 },
});
await server.listen();
server.printUrls();
