import { ref, watch, type Ref, type ComputedRef } from 'vue'
import { getChatRoster, sendChatMessage, type ChatRosterEntry } from '../api'

/** Server roster is authoritative. Discard responses for departed scenes/sessions. */
export function useSceneChat(sessionId: Ref<string | null>, inGame: ComputedRef<boolean>, state: Ref<Record<string, unknown>>, busy: Ref<boolean>, error: Ref<string>) {
  const chatRoster = ref<ChatRosterEntry[]>([])
  const selectedCharacter = ref('')
  const chatMessages = ref<Array<{ player: string; reply: string }>>([])
  const chatBusy = ref(false)
  const chatInput = ref('')
  const chatOpen = ref(false)
  let rosterRequest = 0
  let messageRequest = 0

  function onCharacterChange() {
    messageRequest++
    chatBusy.value = false
    chatMessages.value = []
    chatInput.value = ''
  }
  async function refreshChatRoster() {
    const request = ++rosterRequest
    const session = sessionId.value
    if (!session || !inGame.value) {
      chatRoster.value = []
      selectedCharacter.value = ''
      onCharacterChange()
      chatOpen.value = false
      return
    }
    try {
      const roster = await getChatRoster(session)
      if (request !== rosterRequest || session !== sessionId.value) return
      chatRoster.value = roster
      if (!roster.some(item => item.name === selectedCharacter.value)) {
        selectedCharacter.value = ''
        onCharacterChange()
      }
    } catch (cause) {
      if (request !== rosterRequest || session !== sessionId.value) return
      chatRoster.value = []
      selectedCharacter.value = ''
      onCharacterChange()
      error.value = cause instanceof Error ? cause.message : '场景角色列表加载失败'
    }
  }
  async function sendChat() {
    const message = chatInput.value.trim()
    const session = sessionId.value
    const character = selectedCharacter.value
    if (!message || !session || busy.value || chatBusy.value || !chatRoster.value.some(item => item.name === character)) return
    const request = ++messageRequest
    chatBusy.value = true
    error.value = ''
    try {
      const result = await sendChatMessage(session, character, message)
      if (request !== messageRequest || session !== sessionId.value || character !== selectedCharacter.value) return
      chatMessages.value.push({ player: message, reply: result.reply })
      chatInput.value = ''
    } catch (cause) {
      if (request === messageRequest) error.value = cause instanceof Error ? cause.message : '角色回复失败'
    } finally {
      if (request === messageRequest) chatBusy.value = false
    }
  }
  watch([sessionId, inGame, () => state.value.revision, () => state.value.scene, () => state.value.active_members], () => {
    void refreshChatRoster()
  }, { deep: true })
  return { chatRoster, selectedCharacter, chatMessages, chatBusy, chatInput, chatOpen, sendChat, onCharacterChange }
}
