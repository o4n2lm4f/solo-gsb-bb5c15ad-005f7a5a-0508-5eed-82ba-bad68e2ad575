# 跑道离场尾流间隔校验 API

基于 **Python + FastAPI + SQLite** 的跑道离场尾流间隔校验服务：

- 客户端提交按跑道分组的航班计划（唯一航班号、计划离场时刻、尾流等级）及前机—后机最小间隔矩阵；
- 服务端按 **计划时刻 + 航班号** 稳定排序，列出同一跑道 **相邻航班** 的间隔违规；
- **试排接口** 在保持原顺序的前提下，将每项航班推迟至最早安全时刻，延误沿队列 **向后级联**，且 **不改写原计划**；
- 返回调整后时刻、单项延误（秒）与触发延误的前机航班号。

## 一键启动

```bash
docker compose up --build
```

启动后访问：

- API 地址： http://localhost:8000
- 交互式文档（Swagger UI）： http://localhost:8000/docs
- 健康检查： http://localhost:8000/healthz

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_PATH` | 容器内 `/data/wakegap.db`（本地运行为 `./wakegap.db`） | SQLite 数据库文件路径 |

`docker-compose.yml` 已将 `/data` 挂载为命名卷 `wakegap-data`，重启容器数据不丢失；端口映射为 `8000:8000`。

## 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/plans` | 提交计划，返回 `plan_id` 与各跑道排序结果、间隔违规 |
| GET | `/api/v1/plans/{plan_id}` | 读取已存储的原始计划 |
| GET | `/api/v1/plans/{plan_id}/violations` | 重新计算并列出间隔违规 |
| POST | `/api/v1/plans/{plan_id}/trial` | 试排：保序推迟至最早安全时刻，延误级联，不改写原计划 |
| GET | `/healthz` | 健康检查 |

### 提交计划示例

```bash
curl -X POST http://localhost:8000/api/v1/plans \
  -H "Content-Type: application/json" \
  -d @examples/sample_plan.json
```

请求体结构：

```json
{
  "name": "早高峰离场示例",
  "separation_matrix": {
    "HEAVY":  {"HEAVY": 96, "MEDIUM": 120, "LIGHT": 180},
    "MEDIUM": {"HEAVY": 60, "MEDIUM": 60,  "LIGHT": 120},
    "LIGHT":  {"HEAVY": 60, "MEDIUM": 60,  "LIGHT": 60}
  },
  "runways": {
    "18L": [
      {"flight_no": "CA101", "sched_time": "2026-09-18T08:00:00Z", "wake_category": "HEAVY"},
      {"flight_no": "MU202", "sched_time": "2026-09-18T08:01:00Z", "wake_category": "MEDIUM"}
    ]
  }
}
```

校验规则：

- `flight_no` 在整个计划内唯一；
- `wake_category` 统一按大写处理；
- `separation_matrix` 必须覆盖所有实际出现的等级组合（前机 × 后机），间隔为非负整数秒；
- `sched_time` 为 ISO 8601 时间，无时区时按 UTC 处理。

### 试排示例

```bash
curl -X POST http://localhost:8000/api/v1/plans/1/trial
```

响应（节选）：

```json
{
  "plan_id": 1,
  "runways": {
    "18L": [
      {"flight_no": "CA101", "sched_time": "2026-09-18T08:00:00+00:00",
       "adjusted_time": "2026-09-18T08:00:00+00:00", "delay_seconds": 0, "triggered_by": null},
      {"flight_no": "MU202", "sched_time": "2026-09-18T08:01:00+00:00",
       "adjusted_time": "2026-09-18T08:02:00+00:00", "delay_seconds": 60, "triggered_by": "CA101"}
    ]
  },
  "total_delay_seconds": 360
}
```

试排算法：按稳定排序后的顺序遍历，每班最早安全时刻为
`max(原计划时刻, 前机调整后时刻 + 前机→本机最小间隔)`；
因此前机延误会自动向后级联，且全程只读原始计划，数据库中的计划时刻保持不变。

## 本地开发（可选）

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

运行测试：

```bash
pip install pytest httpx
python -m pytest tests/ -v
```
