import prisma from "@/lib/db";
import { hashToken } from "./tokens";

type AuthSuccess = { ok: true; userId: string; scopes: string[]; tokenName: string };
type AuthFailure = { ok: false; status: 401 | 403; error: string };

export async function resolveMcpToken(req: Request): Promise<AuthSuccess | AuthFailure> {
  const authHeader = req.headers.get("authorization") ?? "";
  if (!authHeader.startsWith("Bearer ")) {
    return { ok: false, status: 401, error: "Missing or malformed Authorization header" };
  }

  const plaintext = authHeader.slice("Bearer ".length).trim();
  if (!plaintext) {
    return { ok: false, status: 401, error: "Empty token" };
  }

  const tokenHash = hashToken(plaintext);
  const record = await prisma.mcpAccessToken.findUnique({ where: { tokenHash } });

  if (!record) {
    return { ok: false, status: 401, error: "Invalid token" };
  }

  if (record.expiresAt < new Date()) {
    return { ok: false, status: 401, error: "Token expired" };
  }

  // Fire-and-forget lastUsedAt update
  prisma.mcpAccessToken.update({
    where: { id: record.id },
    data: { lastUsedAt: new Date() },
  }).catch(() => {});

  let scopes: string[];
  try {
    const parsed: unknown = JSON.parse(record.scopes);
    // a row holding a bare JSON string parses to a String, whose .includes() then
    // passes every scope gate by substring
    if (!Array.isArray(parsed) || parsed.some((s) => typeof s !== "string")) {
      return { ok: false, status: 401, error: "Malformed token scopes" };
    }
    scopes = parsed as string[];
  } catch {
    return { ok: false, status: 401, error: "Malformed token scopes" };
  }
  return { ok: true, userId: record.userId, scopes, tokenName: record.name };
}
