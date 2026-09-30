import { PrismaClient } from "@prisma/client";
import { generateToken } from "./src/lib/mcp/tokens.ts";

const name = process.argv[2] || "autopilot";
const days = Number(process.argv[3] || 365);
const scopes = ["jobs:write", "questions:write", "resume:write"];

const prisma = new PrismaClient();
const user = await prisma.user.findFirst({ orderBy: { id: "asc" } });
if (!user) {
  console.error("no JobSync account yet");
  process.exit(1);
}

const { plaintext, hash, prefix } = generateToken();
await prisma.mcpAccessToken.create({
  data: {
    userId: user.id,
    name,
    tokenHash: hash,
    tokenPrefix: prefix,
    scopes: JSON.stringify(scopes),
    expiresAt: new Date(Date.now() + days * 24 * 60 * 60 * 1000),
  },
});

console.log(plaintext);
await prisma.$disconnect();
