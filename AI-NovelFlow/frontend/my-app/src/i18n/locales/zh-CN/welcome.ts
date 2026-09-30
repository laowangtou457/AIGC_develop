// 欢迎页翻译
export default {
  // 欢迎页
  welcome: {
    title: '欢迎使用 FXAI',
    subtitle: 'AI 驱动的小说到动画/视频工作流',
    features: {
      novelManagement: {
        title: '📚 小说管理',
        desc: '上传和管理小说文本，支持 TXT、EPUB 格式，自动解析章节结构',
      },
      characterLibrary: {
        title: '👥 角色库',
        desc: 'AI 自动提取角色信息，生成角色人设图和参考图',
      },
      storyboard: {
        title: '🎬 智能分镜',
        desc: '自动将章节拆分为分镜，生成 AI 绘图提示词',
      },
      comfyUI: {
        title: '🎨 ComfyUI 集成',
        desc: '一键发送到 ComfyUI 生成图片和视频',
      },
      workflow: {
        title: '⚡ 工作流自动化',
        desc: '支持批量生成、任务队列管理',
      },
    },
    getStarted: '开始使用',
    quickActions: '快捷操作',
    recentNovels: '最近的小说',
    systemStatus: '系统状态',
    pleaseConfigure: '请先完成系统配置，才能开始使用 FXAI。',

    // 工作流节点（H3 漫剧工作流）
    workflow: {
      importNovel: '导入小说',
      generateScript: '生成剧本',
      editChapter: '编辑章节',
      h3SplitShots: '生成文字分镜',
      h3PromptShots: '生成分镜提示词',
      generateVideo: '生成视频',
      parseCharacters: 'AI解析角色',
      parseScenes: 'AI解析场景',
      parseProps: 'AI解析道具',
      generateCharacters: '生成角色图',
      generateScenes: '生成场景图',
      generateProps: '生成道具图',
      parallelSection: '并行执行区间（与主流程同步）',
      materialInject: '角色 / 场景 / 道具素材注入（汇入生成视频）',
    },
  },
};