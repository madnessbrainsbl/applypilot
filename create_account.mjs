import bcrypt from "bcryptjs";
import { PrismaClient } from "@prisma/client";
import { JOB_SOURCES, CONTACT_ROLES, JOB_STATUSES } from "./src/lib/constants.ts";

const [name, email, password] = process.argv.slice(2);
if (!name || !email || !password) {
  console.error("usage: node create_account.mjs <name> <email> <password>");
  process.exit(1);
}

const prisma = new PrismaClient();

const existing = await prisma.user.findUnique({ where: { email } });
if (existing) {
  console.log(`account already exists: ${email} (${existing.id})`);
  await prisma.$disconnect();
  process.exit(0);
}

const user = await prisma.user.create({
  data: { name, email, password: await bcrypt.hash(password, 10) },
});

await prisma.jobSource.createMany({
  data: JOB_SOURCES.map((s) => ({ label: s.label, value: s.value, createdBy: user.id })),
});
await prisma.contactRole.createMany({
  data: CONTACT_ROLES.map((r) => ({ label: r.label, value: r.value, createdBy: user.id })),
});
for (const status of JOB_STATUSES) {
  await prisma.jobStatus.upsert({ where: { value: status.value }, update: {}, create: status });
}

console.log(`created ${email} (${user.id}) with ${JOB_SOURCES.length} sources and ${JOB_STATUSES.length} statuses`);
await prisma.$disconnect();
