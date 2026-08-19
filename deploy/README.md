# Deployment

```bash
sudo bash deploy/bootstrap.sh
read -r -p "GHCR username: " GHCR_USERNAME
read -r -s -p "GHCR read token: " GHCR_TOKEN
printf '%s' "$GHCR_TOKEN" | sudo docker login ghcr.io --username "$GHCR_USERNAME" --password-stdin
unset GHCR_TOKEN
read -r -p "Published sha256 digest: " IMAGE_DIGEST
[[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 2
sudo /opt/placegame-mcp/bin/deploy "$IMAGE_DIGEST"
curl --fail http://127.0.0.1:18080/health/live
curl --fail http://127.0.0.1:18080/health/ready
```

The GHCR token needs `read:packages` only. Database downgrade is manual. The routine deploy command never accepts a token.
