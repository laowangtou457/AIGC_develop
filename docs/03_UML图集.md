# AI-NovelFlow 影视智能创作平台 — UML 图集

> 全部图形使用 Mermaid 语法（v10+），可在 VS Code（Markdown Preview Mermaid Support）、Typora、GitHub 中直接渲染。
> 本文档是 `00_软件开发文档.md` 的完整展开版。v1.0 为 H3 集成图集（第 1-7 章），v2.0 追加三大工作流架构图（第 8 章），v2.1 追加第四工作流架构与时序图（第 9 章）。

---

## 目录

1. [用例图](#1-用例图)
2. [类图](#2-类图)
3. [时序图](#3-时序图)
4. [活动图](#4-活动图)
5. [组件图](#5-组件图)
6. [部署图](#6-部署图)
7. [ER 图（数据模型）](#7-er-图数据模型)

---

## 1. 用例图

> Mermaid 暂无原生 use case 语法，用 flowchart 表达参与者（actor）与用例关系。

```mermaid
flowchart LR
    Actor["创作者/用户"] ---|include| A["导入小说"]
    Actor --- B["生成剧本（编辑章节）"]
    Actor --- C["生成文字分镜（H3）"]
    Actor --- D["生成分镜提示词（H3）"]
    Actor --- E["生成视频"]
    Actor --- F["AI解析角色/场景/道具"]
    Actor --- G["生成角色/场景/道具图"]

    A --> B
    B --> C
    C --> D
    D --> E
    F --> G
    G -.素材注入.-> E
```

**用例说明**：

| 用例 | 主路径 | 前置 | 系统交互 |
|---|---|---|---|
| 生成文字分镜（H3） | 点击「H3 生成文字分镜」→ 确认 → 等待生成 | 章节有内容、LLM 已配置 | POST `/h3-split` → 清旧分镜 → 落库 Shot → 返回分镜列表 |
| 生成分镜提示词（H3） | 点击「生成分镜提示词（H3）」→ 确认 | 已生成 H3 分镜 | POST `/h3-prompt-all` → 逐镜生成 → 写入 video_description → 刷新列表 |
| 生成视频 | 视频生成 Tab 执行 | 分镜有 H3 提示词、ComfyUI 运行 | 复用原有 video workflow（H3 格式） |

---

## 2. 类图

### 2.1 后端核心类

```mermaid
classDiagram
    class H3ManjuService {
        +script_to_storyboard()
        +storyboard_to_shot_prompt()
        +build_default_preset()
        +shot_to_dialogue_list()
        -llm
    }
    class LLMService {
        +chat_completion()
    }
    class ManjuEngine {
        +build_manju_system_prompt()
        +extract_json()
        +compute_shot_refs()
        +build_wiring_note()
        +parse_mapping_text()
    }
    class H3WorkflowAPI {
        +h3_split_chapter()
        +h3_prompt_all_shots()
        +h3_prompt_one_shot()
        +h3_parse_mapping()
    }
    class ShotRepository {
        +create()
        +update()
        +get_by_chapter()
        +delete_by_chapter()
    }
    class Shot {
        +id
        +chapter_id
        +index
        +description
        +video_description
        +duration
    }
    class Chapter {
        +id
        +title
        +content
        +parsed_data
    }

    H3ManjuService --> LLMService : 组合
    H3ManjuService ..> ManjuEngine : 纯函数复用
    H3WorkflowAPI --> H3ManjuService : 编排
    H3WorkflowAPI --> ShotRepository : 持久化
    H3WorkflowAPI --> Chapter : 读写
    ShotRepository --> Shot : CRUD
```

### 2.2 前端核心类型（Zustand store 切片）

```mermaid
classDiagram
    class ChapterGenerateStore {
        +splitChapter() Promise
        +h3SplitChapter() Promise
        +h3PromptAllShots() Promise
        +setShots() void
        +setParsedData() void
        +shots
        +parsedData
    }
    class ChapterActionsSlice {
        +h3SplitChapter()
        +h3PromptAllShots()
        +splitChapter()
    }
    class ShotSplitTab {
        +handleSplit()
        +confirmSplit()
        +saveShotsData()
        +h3SplitChapter
        +h3PromptAllShots
    }
    class Shot {
        +id
        +index
        +description
        +video_description
        +characters
        +scene
        +props
        +duration
        +dialogues
    }
    ChapterActionsSlice ..|> ChapterGenerateStore : 组合进 store
    ShotSplitTab --> ChapterGenerateStore : 订阅/调用
    ShotSplitTab ..> Shot : 渲染列表
```

---

## 3. 时序图

### 3.1 剧本 → 文字分镜（h3-split）

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户
    participant FE as ShotSplitTab.tsx
    participant STORE as ChapterActionsSlice
    participant API as h3_workflow.py
    participant SVC as H3ManjuService
    participant ENG as manju_nodes(纯函数)
    participant LLM as LLMService→DeepSeek
    participant DB as SQLite

    User->>FE: 点击「H3 生成文字分镜」
    FE->>FE: window.confirm（清空提示）
    FE->>STORE: h3SplitChapter(novelId, chapterId)
    STORE->>STORE: set(isSplitting, 清空 shots)
    STORE->>API: POST /h3-split {script: chapter.content}
    API->>DB: 查询章节/小说
    API->>SVC: script_to_storyboard(script, preset)
    SVC->>ENG: build_manju_system_prompt("manju_storyboard.txt")
    SVC->>LLM: chat_completion(system+user)
    LLM-->>SVC: 分镜 JSON 文本
    SVC->>ENG: _extract_json / 校验 shots
    SVC-->>API: {storyboard_json, assets_json, summary}
    API->>DB: 清空旧分镜（资源目录+Shot 行）
    loop 每个分镜
        API->>DB: shot_repo.create(...)（字段映射）
    end
    API->>DB: 写 chapter.parsed_data（h3_storyboard/h3_assets）
    API-->>STORE: {success, data:{shots[], ...}}
    STORE->>STORE: set(shots, parsedData, editableJson)
    STORE-->>FE: 渲染分镜列表
```

### 3.2 分镜 → 逐镜 H3 提示词（h3-prompt-all）

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户
    participant FE as ShotSplitTab.tsx
    participant STORE as ChapterActionsSlice
    participant API as h3_workflow.py
    participant SVC as H3ManjuService
    participant LLM as LLMService→DeepSeek
    participant DB as SQLite

    User->>FE: 点击「生成分镜提示词（H3）」
    FE->>STORE: h3PromptAllShots(novelId, chapterId, '{}')
    STORE->>API: POST /h3-prompt-all {mapping_json:"{}"}
    API->>DB: 读 chapter.parsed_data → h3_storyboard
    API->>DB: 读该章全部分镜（index 排序）
    loop 每个 Shot
        API->>SVC: storyboard_to_shot_prompt(sb_json, mapping, index)
        SVC->>SVC: compute_shot_refs → Picture N 接线
        SVC->>LLM: chat_completion(manju_shot_prompt 规则)
        LLM-->>SVC: h3_prompt
        API->>DB: update shot.video_description = h3_prompt
    end
    API-->>STORE: {results:[{index,success,h3_prompt,wiring_note}]}
    STORE->>API: GET /shots（拉最新）
    API-->>STORE: shots[]
    STORE->>STORE: set(shots)
    STORE-->>FE: 渲染（提示词已写入视频描述）
```

### 3.3 资源映射解析（h3-mapping）

```mermaid
sequenceDiagram
    autonumber
    participant C as 调用方（API/前端后续）
    participant API as h3_workflow.py
    participant ENG as manju_nodes.parse_mapping_text

    C->>API: POST /h3-mapping {text:"角色A=图1\n角色B=图2"}
    API->>ENG: parse_mapping_text(text, assets_json)
    ENG->>ENG: 归一化分隔符 / 解析"名=图N" / 重复/缺失告警
    ENG-->>API: mapping JSON 字符串
    API-->>C: {success, data:{mapping_json, mapping}}
```

---

## 4. 活动图

### 4.1 章节生成主流程（含并行区间）

```mermaid
flowchart TB
    S((开始)) --> A[导入小说]
    A --> B[生成剧本/编辑章节]
    B --> C[生成文字分镜（H3）]
    C --> D[生成分镜提示词（H3）]
    D --> E[生成视频]
    E --> F((完成))

    subgraph Parallel["并行执行区间（与主流程同步）"]
        P1[AI解析角色] --> P1G[生成角色图]
        P2[AI解析场景] --> P2G[生成场景图]
        P3[AI解析道具] --> P3G[生成道具图]
    end
    B -.触发.-> Parallel
    P1G -.素材注入.-> E
    P2G -.素材注入.-> E
    P3G -.素材注入.-> E
```

### 4.2 h3-split 内部活动（后端）

```mermaid
flowchart TB
    START([POST /h3-split]) --> V1{章节存在?}
    V1 -- 否 --> E404[404 章节不存在]
    V1 -- 是 --> V2{剧本非空?}
    V2 -- 否 --> E400[400 剧本内容为空]
    V2 -- 是 --> SB[script_to_storyboard]
    SB --> V3{LLM 成功?}
    V3 -- 否 --> ELM[success:false + 错误 message]
    V3 -- 是 --> V4{JSON 含 shots?}
    V4 -- 否 --> EJ[分镜 JSON 解析失败]
    V4 -- 是 --> CLR[清空旧分镜/资源目录]
    CLR --> LOOP[逐镜创建 Shot 记录]
    LOOP --> SAVE[写 chapter.parsed_data]
    SAVE --> OK[success:true + shots]
```

### 4.3 用户操作决策（前端）

```mermaid
flowchart TD
    U([用户在分镜拆分 Tab]) --> C{点击?}
    C -->|H3 生成文字分镜| CF1[confirm 清空提示]
    CF1 -->|取消| U
    CF1 -->|确认| S1[调 h3-split]
    S1 --> R1{成功?}
    R1 -->|是| OK1[更新 shots/parsedData]
    R1 -->|否| ERR1[toast 错误]
    C -->|生成分镜提示词（H3）| CF2[confirm 覆盖提示]
    CF2 -->|取消| U
    CF2 -->|确认| S2[调 h3-prompt-all]
    S2 --> R2{成功?}
    R2 -->|是| OK2[重新拉取 shots]
    R2 -->|否| ERR2[toast 错误]
    C -->|✨ AI 拆分（原）| S3[原 splitChapter]
```

---

## 5. 组件图

```mermaid
flowchart LR
    subgraph FE["前端 React（:5173）"]
        PG["ChapterGenerate 页面"]
        SPLIT["ShotSplitTab"]
        STORE2["Zustand Store<br/>chapterActionsSlice"]
        API2["fetch /api（vite 代理）"]
        PG --> SPLIT
        SPLIT --> STORE2
        STORE2 --> API2
    end

    subgraph BE["后端 FastAPI（:8000）"]
        ROUTER["APIRouter 集合<br/>h3_workflow / chapters / shots"]
        H3SVC["H3ManjuService"]
        ENG["h3_prompt_builder 引擎"]
        LLMSVC["LLMService"]
        REPO["Repositories<br/>Shot/Chapter/Novel"]
        ROUTER --> H3SVC
        H3SVC --> ENG
        H3SVC --> LLMSVC
        ROUTER --> REPO
    end

    API2 -->|HTTP /api| ROUTER
    LLMSVC -->|HTTPS| DEEP["DeepSeek API"]
    REPO --> DB[("SQLite novelflow.db")]
    ROUTER -->|HTTP :8188| CUI["ComfyUI"]
```

---

## 6. 部署图

```mermaid
flowchart TB
    subgraph Host["Windows 主机（用户 PC）"]
        subgraph Node1["后端节点 :8000"]
            U1["Uvicorn 进程"]
            APP1["FastAPI app.main"]
            D1[("novelflow.db")]
            R1["h3_prompt_builder/rules/"]
        end
        subgraph Node2["前端节点 :5173"]
            V1["Vite Dev Server"]
            B1["React SPA（构建产物 dist/ 可用）"]
        end
        subgraph Node3["ComfyUI 节点 :8188（可选）"]
            C1["ComfyUI 服务"]
        end
    end
    EXT["外部：DeepSeek LLM API（HTTPS）"]

    V1 -->|"/api 代理"| APP1
    APP1 --> D1
    APP1 --> R1
    APP1 --> C1
    APP1 --> EXT
    B1 -.静态托管备选.-> V1
```

**部署要点**：
- 后端依赖：Python 3.12 + requirements.txt（fastapi/sqlalchemy/uvicorn 等）
- 前端依赖：node_modules（已随备份复制）；`npm run dev` 开发、`npm run build` 出 dist
- 数据库：SQLite 单文件 `novelflow.db`，随项目复制，无需安装
- 外部：DeepSeek API（需 api_key）；ComfyUI 仅出图/视频需要

---

## 7. ER 图（数据模型）

> 仅展示与 H3 链路相关的核心实体与关系；其余表（tasks/workflows/prompt_templates 等）省略。

```mermaid
erDiagram
    NOVEL ||--o{ CHAPTER : "contains"
    CHAPTER ||--o{ SHOT : "contains"
    CHAPTER ||--o{ CHARACTER : "parsed"
    CHAPTER ||--o{ SCENE : "parsed"
    CHAPTER ||--o{ PROP : "parsed"
    SHOT }o--|| CHAPTER : "belongs to"

    NOVEL {
        string id PK "uuid"
        string title "小说标题"
        string description "简介"
        string status "pending/processing/completed"
    }
    CHAPTER {
        string id PK "uuid"
        string novel_id FK "所属小说"
        int number "章节序号"
        string title "章节标题"
        text content "章节/剧本正文"
        text parsed_data "解析数据 JSON"
        text shot_images "分镜图(旧字段)"
        text shot_videos "分镜视频(旧字段)"
    }
    SHOT {
        string id PK "uuid"
        string chapter_id FK "所属章节"
        int index "分镜序号 1-based"
        text description "分镜描述(action/purpose)"
        text video_description "视频提示词(H3 写入)"
        text characters "角色名 JSON 数组"
        string scene "场景名"
        text props "道具名 JSON 数组"
        int duration "时长(秒)"
        text dialogues "台词 JSON 数组"
        string continuity_mode "NORMAL/CONTINUOUS_TAKE"
        text video_director_plan "视频导演计划 JSON"
        string image_url "分镜图 URL"
        string image_status "pending/generating/completed/failed"
        string video_url "视频 URL"
        string video_status "pending/generating/completed/failed"
    }
    CHARACTER {
        string id PK "uuid"
        string novel_id FK
        string name "角色名"
        text appearance "外观描述"
        string image_url "角色图"
    }
    SCENE {
        string id PK "uuid"
        string novel_id FK
        string name "场景名"
        text description
        string image_url "场景图"
    }
    PROP {
        string id PK "uuid"
        string novel_id FK
        string name "道具名"
        text description
        string image_url "道具图"
    }
```

**H3 相关存储约定**：
- `chapters.parsed_data` 增补键：`h3_storyboard`（完整分镜 JSON）、`h3_assets`（角色/场景/道具清单）
- `shots.video_description`：H3 提示词最终写入位置（视频生成 Tab 的输入）
- 不新增表、不新增列 → 无数据库迁移

---

## 附：渲染提示

- VS Code：安装扩展 "Markdown Preview Mermaid Support" 或 "Markdown Preview Enhanced"
- Typora：内置 Mermaid 支持
- GitHub：仓库内直接渲染 .md 的 Mermaid
- 若编辑器不支持 Mermaid，可复制代码块到 https://mermaid.live 在线渲染

---

## 8. 三大工作流架构图（v2.0 追加）

### 8.1 平台系统架构（含三工作流）

```mermaid
flowchart TB
    subgraph Browser["浏览器 React :5173"]
        W["欢迎页 / 小说管理 / 章节生成页"]
        M["武术指导 /martial-arts"]
        V["视频资源替换 /video-asset-swap"]
        MON["任务进程监控 /monitor"]
    end

    subgraph BE["AI-NovelFlow 后端 FastAPI :8000"]
        H3["h3_workflow 路由<br/>H3ManjuService + h3_prompt_builder 规则库"]
        MA["martial_arts 路由<br/>video_director_ai（锚定卡/16宫格/生图编排）"]
        VA["video_asset 路由<br/>VideoAssetHistory 模型"]
        GS["gpu_scheduler（GPU 串行调度）"]
        TASK["任务系统 / 监控聚合"]
    end

    subgraph Ext["外部本地服务"]
        OLL["Ollama :11434<br/>qwen3:8b / 30b-a3b / qwen2.5vl"]
        CUI["ComfyUI :8188<br/>Flux2-4B 生图 / MiniMax H3 生视频"]
        PIPE["ManjuToSplitFrameAndProperty<br/>视频解析管线（独立 venv 子进程）"]
    end

    DB[("SQLite novelflow.db")]

    W --> BE
    M --> BE
    V --> BE
    MON --> BE
    H3 --> OLL
    MA --> OLL
    MA --> CUI
    VA --> PIPE
    VA --> DB
    H3 --> DB
    MA --> DB
    GS -.全局锁.-> H3
    GS -.全局锁.-> MA
    PIPE -.产物JSON只读.-> VA

---

## 9. 第四工作流架构图：提示词提取与重构（v2.1 追加）

### 9.1 平台系统架构（含四工作流）

```mermaid
flowchart TB
    subgraph Browser["浏览器 React :5173"]
        W["欢迎页 / 小说管理 / 章节生成页"]
        M["武术指导 /martial-arts"]
        V["视频资源替换 /video-asset-swap"]
        P["提示词提取与重构 /prompt-reforge"]
        MON["任务进程监控 /monitor"]
    end

    subgraph BE["AI-NovelFlow 后端 FastAPI :8000"]
        H3["h3_workflow 路由<br/>H3ManjuService + h3_prompt_builder 规则库"]
        MA["martial_arts 路由<br/>video_director_ai（锚定卡/16宫格/生图编排）"]
        VA["video_asset 路由<br/>VideoAssetHistory 模型"]
        PR["prompt_reforge 路由<br/>PromptReforgeService（导演模型注册表）"]
        GS["gpu_scheduler（GPU 串行调度）"]
        TASK["任务系统 / 监控聚合"]
    end

    subgraph Ext["外部本地服务"]
        OLL["Ollama :11434<br/>qwen3:8b / 30b-a3b / qwen2.5vl"]
        CUI["ComfyUI :8188<br/>Flux2-4B 生图 / MiniMax H3 生视频"]
        PIPE["ManjuToSplitFrameAndProperty<br/>视频解析管线（独立 venv 子进程）"]
    end

    subgraph REF["H3 官方规则存档"]
        REF1["docs/references/ref-en.txt（Ref2VA 六段式）"]
        REF2["docs/references/base-en.txt（I2VA 三字段）"]
    end

    DB[("SQLite novelflow.db")]

    W --> BE
    M --> BE
    V --> BE
    P --> BE
    MON --> BE
    H3 --> OLL
    MA --> OLL
    MA --> CUI
    VA --> PIPE
    PR --> OLL
    PR -.运行时读取.-> REF1
    PR -.运行时读取.-> REF2
    VA --> DB
    H3 --> DB
    MA --> DB
    PR --> DB
    GS -.全局锁.-> H3
    GS -.全局锁.-> MA
    PIPE -.产物JSON只读.-> VA
```

### 9.2 提示词重构时序图（三阶段）

```mermaid
sequenceDiagram
    participant U as 用户（前端 /prompt-reforge）
    participant A as prompt_reforge API
    participant S as PromptReforgeService
    participant L as Ollama qwen3:8b
    participant F as 产物目录 data/prompt_reforge

    U->>A: POST /api/prompt-reforge/tasks（文本/文件 + 导演模型 + 目标平台）
    A->>S: 创建任务（status=queued），入 worker 队列
    S->>L: ① _extract_beats（response_format=json_object）
    L-->>S: 剧本节拍 JSON（角色/场景/动作/运镜/氛围/对白）
    S->>L: ② _reforge_prompts（导演模型规则注入）
    L-->>S: 逐镜提示词（### 平台名 分区）
    S->>S: _split_platform_parts（白名单拆包 5 平台）
    S->>F: ③ _pack_outputs：prompts.md + report.json + payloads/shot_NNN.json
    S->>A: status=success，返回任务详情
    U->>A: GET /tasks/{id}（3s 轮询）
    A-->>U: report/platform_parts/产物文件树
    U->>A: GET /tasks/{id}/files/prompts.md（下载）

