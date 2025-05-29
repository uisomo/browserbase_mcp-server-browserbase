import { createServer } from './dist/server.js';
import { StreamableHttpServerTransport } from '@modelcontextprotocol/sdk/server/streamable_http.js';

const PORT = process.env.PORT || 8080;
const server = createServer();
const transport = new StreamableHttpServerTransport({
  host: '0.0.0.0',
  port: PORT,
});

await server.connect(transport);
console.log(`Stagehand MCP HTTP server listening on 0.0.0.0:${PORT}`);
