import type {Voice} from './api';

// Older unnamed records include the short ID in display_name.
export function editableVoiceName(voice: Pick<Voice,'id'|'display_name'>) {
  const name=voice.display_name || '我的声音';
  return name===`我的声音 · ${voice.id.slice(0,8)}`?'我的声音':name;
}
export function voiceLabel(voice: Pick<Voice,'id'|'display_name'>) {
  return `${editableVoiceName(voice)} · ${voice.id.slice(0,8)}`;
}
