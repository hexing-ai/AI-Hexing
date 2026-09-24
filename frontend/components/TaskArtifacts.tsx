import type { Task } from '../lib/api';

function textState(value: unknown, stale = false): string {
  if (!value) return '未生成';
  if (typeof value !== 'object' || Array.isArray(value)) return '待核对';
  const version = value as Record<string, unknown>;
  if (stale || version.context_stale === true) return '待复核';
  return typeof version.confirmed_at === 'string' && version.confirmed_at ? '已确认' : '草稿';
}
function audioState(task: Task): string {
  if (!task.current_audio) return '未生成';
  if (task.audio_stale) return '待更新';
  if (typeof task.current_audio !== 'object' || Array.isArray(task.current_audio)) return '待核对';
  const audio = task.current_audio as Record<string, unknown>;
  if (typeof audio.status === 'string' && audio.status) return audio.status;
  return typeof audio.confirmed_at === 'string' && audio.confirmed_at ? '已试听确认' : '待查看与试听';
}
export default function TaskArtifacts({ task }: { task: Task }) {
  return <div className="task-artifacts" aria-label="任务素材状态">
    <span>资料 · 已复核</span>
    <span>口播 · {textState(task.current_script)}</span>
    <span>音频 · {audioState(task)}</span>
    <span>文案 · {textState(task.current_copy, task.copy_stale)}</span>
    {task.materials_ready === true && <strong>{task.status === 'mock_ready' ? '模拟素材就绪' : '素材就绪'}</strong>}
  </div>;
}
