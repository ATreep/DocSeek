[English](README.en-US.md)

# DocSeek

## 简介

DocSeek 是一个本地知识库管理系统 —— 可视化关系图谱生成 & AI 知识问答。
借助 LLM 与 GraphRAG，自动为文档分组整理、提取文档内有价值的概念实体、生成实体关系图谱、跨文档 AI 聊天查询。

## Demo 项目
仓库预置了两个 Demo 演示项目，无需配置 Model Provider 即可一览演示示例。

## 快速开始

环境要求：`uv`、Node.js 20+、npm 和 Git。

```bash
git clone https://github.com/ATreep/DocSeek.git
cd DocSeek
uv python install 3.12
uv sync --group dev
npm --prefix frontend install
./start.sh
```

启动完成后访问 <http://localhost:5173>，使用本地开发账号 `admin` / `admin` 登录。首次登录后请立即修改密码；不要在未加固的情况下将开发服务暴露到公网。

`start.sh` 会启动 API（默认 `http://127.0.0.1:8000`）和前端，并将运行数据写入 `data/`。

## 主要功能

- **资产导入与解析**：支持文本、PDF、Office 文档等常见格式，保留项目和资产层级。
- **资产图谱**：展示资产之间的关系，支持筛选、搜索、缩放、聚焦和重新布局。
- **实体图谱**：从资产内容中提取实体、定义和关系，并保留来源资产。
- **搜索与 AI 查询**：在资产和实体中检索；AI 查询基于图谱证据回答，并提供引用和关系路径。
- **项目级 MCP**：开启 DocSeek 的 MCP 服务器，为第三方 Agent 提供知识库管理的相关工具。
- **角色权限管理**：支持严格的角色分组权限管理，面相企业用户和多人组织。

## 屏幕截图

### AI 查询

![AI 查询](screenshots/ai-query.png)

### 实体图谱总览

![实体图谱总览](screenshots/entity-graph-overview.png)

### 实体关系预览

![实体关系预览](screenshots/entity-relation-preview.png)