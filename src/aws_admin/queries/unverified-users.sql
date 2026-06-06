SELECT id, email, name, "emailVerified", "createdAt"
FROM users
WHERE "emailVerified" IS NULL
ORDER BY "createdAt" DESC;
