import { Book, ChevronRight, FileText, Image as ImageIcon, Package, Sparkles, Users, Video } from 'lucide-react';
import { useTranslation } from '../../../stores/i18nStore';

// H3 漫剧工作流 — 主流程（导入小说 → 生成剧本 → 编辑章节 → 生成文字分镜 → 生成分镜提示词 → 生成视频）
const mainSteps = [
  { icon: Book, color: 'from-sky-500 to-blue-500', key: 'importNovel' },
  { icon: FileText, color: 'from-blue-500 to-indigo-500', key: 'generateScript' },
  { icon: FileText, color: 'from-indigo-500 to-blue-700', key: 'editChapter' },
  { icon: FileText, color: 'from-blue-500 to-indigo-500', key: 'h3SplitShots' },
  { icon: FileText, color: 'from-blue-500 to-indigo-500', key: 'h3PromptShots' },
  { icon: Video, color: 'from-blue-800 to-blue-950', key: 'generateVideo' },
];

// 并行素材区间：AI解析 → 生成图（绿 → 橙）
const parallelGroups = [
  { parse: { icon: Sparkles, color: 'from-green-500 to-emerald-500', key: 'parseCharacters' }, gen: { icon: Users, color: 'from-orange-500 to-amber-500', key: 'generateCharacters' } },
  { parse: { icon: Sparkles, color: 'from-green-500 to-emerald-500', key: 'parseScenes' }, gen: { icon: ImageIcon, color: 'from-orange-500 to-amber-500', key: 'generateScenes' } },
  { parse: { icon: Sparkles, color: 'from-green-500 to-emerald-500', key: 'parseProps' }, gen: { icon: Package, color: 'from-orange-500 to-amber-500', key: 'generateProps' } },
];

export function WorkflowOverview() {
  const { t } = useTranslation();

  return (
    <div>
      {/* 主流程 6 步 */}
      <div className="flex items-center justify-center overflow-x-auto">
        {mainSteps.map((step, index) => (
          <div key={index} className="flex items-center flex-shrink-0">
            <div className="flex flex-col items-center w-[120px]">
              <div className={`w-14 h-14 rounded-xl flex items-center justify-center mb-2 bg-gradient-to-br ${step.color} text-white shadow-md`}>
                <step.icon className="h-7 w-7" />
              </div>
              <span className="text-xs font-medium text-gray-700 text-center whitespace-nowrap">
                {t(`welcome.workflow.${step.key}`)}
              </span>
            </div>
            {index < mainSteps.length - 1 && (
              <div className="flex items-center flex-1 justify-center mx-1 mb-6">
                <ChevronRight className="h-5 w-5 text-gray-300" />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 并行执行区间 */}
      <div className="text-center text-xs text-gray-500 my-3">
        ▲ {t('welcome.workflow.parallelSection')} ▼
      </div>
      <div className="flex justify-center flex-wrap gap-x-8 gap-y-3">
        {parallelGroups.map((group, index) => (
          <div key={index} className="flex items-center gap-2">
            <div className="flex flex-col items-center">
              <div className={`w-11 h-11 rounded-xl flex items-center justify-center mb-1 bg-gradient-to-br ${group.parse.color} text-white shadow-sm`}>
                <group.parse.icon className="h-5 w-5" />
              </div>
              <span className="text-[11px] font-medium text-gray-600 whitespace-nowrap">
                {t(`welcome.workflow.${group.parse.key}`)}
              </span>
            </div>
            <ChevronRight className="h-4 w-4 text-gray-300 mb-4" />
            <div className="flex flex-col items-center">
              <div className={`w-11 h-11 rounded-xl flex items-center justify-center mb-1 bg-gradient-to-br ${group.gen.color} text-white shadow-sm`}>
                <group.gen.icon className="h-5 w-5" />
              </div>
              <span className="text-[11px] font-medium text-gray-600 whitespace-nowrap">
                {t(`welcome.workflow.${group.gen.key}`)}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* 素材注入 */}
      <div className="text-center text-xs text-purple-600 font-medium mt-3">
        ⇢⇢ {t('welcome.workflow.materialInject')} ⇢⇢
      </div>
    </div>
  );
}
