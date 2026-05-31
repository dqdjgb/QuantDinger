# 服务器重新部署流程

本文记录当前云服务器上的 QuantDinger 重新部署流程。当前服务器源码不是 Git 工作区，部署时需要先把本地改动同步到服务器源码目录，再用 Docker Compose 重新构建并启动容器。

## 服务器目录

- 主仓库：`/opt/quantdinger-app/src/QuantDinger`
- 前端仓库：`/opt/quantdinger-app/src/QuantDinger-Vue`
- Compose 文件：
  - `/opt/quantdinger-app/src/QuantDinger/docker-compose.yml`
  - `/opt/quantdinger-app/src/QuantDinger/docker-compose.upload.yml`
- 服务容器：
  - `quantdinger-backend`
  - `quantdinger-frontend`
  - `quantdinger-db`
  - `quantdinger-redis`

## 部署前检查

在本地确认当前分支和待部署内容：

```bash
git status --short --branch
git log --oneline --decorate -5
git diff --name-only origin/main..main
```

在服务器确认当前服务状态：

```bash
cd /opt/quantdinger-app/src/QuantDinger
docker compose -f docker-compose.yml -f docker-compose.upload.yml ps
docker logs quantdinger-backend --tail 80
```

## 同步代码

因为服务器源码目录没有 `.git`，不要在服务器上执行 `git pull`。从本地同步本次变更文件到对应目录：

- `QuantDinger/...` 下的文件同步到 `/opt/quantdinger-app/src/QuantDinger/...`
- `QuantDinger-Vue/...` 下的文件同步到 `/opt/quantdinger-app/src/QuantDinger-Vue/...`

建议同步前在服务器上备份将被覆盖的文件：

```bash
cp path/to/file path/to/file.bak.$(date +%Y%m%d%H%M%S)
```

## 构建并启动

同步完成后，在服务器执行：

```bash
cd /opt/quantdinger-app/src/QuantDinger
python3 -m py_compile \
  backend_api_python/app/routes/auth.py \
  backend_api_python/app/routes/strategy.py \
  backend_api_python/app/services/trading_executor.py \
  backend_api_python/app/utils/timeutil.py \
  backend_api_python/app/data_sources/asia_stock_kline.py \
  backend_api_python/app/data_sources/cn_stock.py

docker compose -f docker-compose.yml -f docker-compose.upload.yml up -d --build
```

说明：

- `docker-compose.upload.yml` 会让前端从 `/opt/quantdinger-app/src/QuantDinger-Vue` 本地源码构建。
- 不要执行 `docker compose pull frontend`，否则可能拉回 GHCR 镜像覆盖本地前端构建结果。
- 数据库和 Redis 使用 Docker volume，正常 `up -d --build` 不会清空数据。

## 验证

部署后检查容器健康状态：

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep quantdinger
curl -sS http://127.0.0.1:5000/api/health
docker logs quantdinger-backend --tail 120
docker logs quantdinger-frontend --tail 80
```

本次 A 股分钟线修复可以额外验证东方财富 15m K 线接口是否有数据：

```bash
docker exec quantdinger-backend python -c "from app.data_sources.cn_stock import CNStockDataSource; rows=CNStockDataSource().get_kline('603618','15m',20); print(len(rows)); print(rows[-1] if rows else None)"
```

期望：

- `quantdinger-backend` 和 `quantdinger-frontend` 均为 `healthy`。
- `/api/health` 返回 `status=healthy`。
- `603618` 的 `15m` K 线返回数量不少于 2。

## 回滚

如果部署后异常：

1. 用 `.bak.<timestamp>` 文件恢复被覆盖的源文件。
2. 重新执行：

```bash
cd /opt/quantdinger-app/src/QuantDinger
docker compose -f docker-compose.yml -f docker-compose.upload.yml up -d --build
```

3. 再次检查容器健康状态和后端日志。
