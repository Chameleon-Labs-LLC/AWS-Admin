SELECT id, email, type, "createdAt", "expiresAt", "usedAt"
FROM verification_tokens
ORDER BY "createdAt" DESC
LIMIT 10;
