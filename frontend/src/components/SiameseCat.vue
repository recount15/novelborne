<template>
  <!-- 暹罗小猫（seal point）：奶油身 + 深棕脸罩/耳/尾，蓝眼。
       纯 SVG + CSS 动画：尾巴摇摆、呼吸、眨眼、偶尔抖耳；hover 时摇尾加速。 -->
  <button
    type="button"
    class="siamese-cat inline-flex shrink-0 select-none items-end"
    aria-label="暹罗小猫"
    :title="title"
    @click="emit('tap')"
  >
    <svg viewBox="0 0 64 52" width="40" height="34" xmlns="http://www.w3.org/2000/svg">
      <!-- 尾巴：origin 在尾根，左右摇摆 -->
      <g class="cat-tail">
        <path
          d="M14 40 Q2 38 4 26 Q5 20 10 19 Q8 25 12 28 Q16 32 18 38 Z"
          fill="#4a362a"
        />
        <path d="M4 26 Q5 20 10 19 Q8.5 24 9 27 Z" fill="#33251c" />
      </g>
      <!-- 身体：呼吸起伏 -->
      <g class="cat-body">
        <ellipse cx="26" cy="41" rx="18" ry="9.5" fill="#f3e5cf" />
        <!-- 暹罗重点色：背部鞍状渐深 -->
        <path d="M14 38 Q26 30 40 39 Q38 44 26 45 Q17 44 14 38 Z" fill="#e8d5b8" opacity="0.8" />
        <!-- 前爪（深色重点） -->
        <ellipse cx="22" cy="48.5" rx="3.4" ry="2.6" fill="#4a362a" />
        <ellipse cx="31" cy="48.5" rx="3.4" ry="2.6" fill="#4a362a" />
      </g>
      <!-- 头组：轻微浮动 -->
      <g class="cat-head">
        <!-- 耳朵（深棕，偶尔抖动） -->
        <g class="cat-ear cat-ear-left">
          <path d="M28 16 L25.5 6.5 L35 12 Z" fill="#4a362a" />
          <path d="M29.5 14.5 L28.3 9.5 L33.5 12.5 Z" fill="#8a6a4f" />
        </g>
        <g class="cat-ear cat-ear-right">
          <path d="M44 16 L46.5 6.5 L37 12 Z" fill="#4a362a" />
          <path d="M42.5 14.5 L43.7 9.5 L38.5 12.5 Z" fill="#8a6a4f" />
        </g>
        <!-- 脸基座（奶油） -->
        <circle cx="36" cy="18" r="11.5" fill="#f3e5cf" />
        <!-- 暹罗脸罩：深色面具覆盖眼周与鼻梁 -->
        <path
          d="M28 12 Q33 8 40 8.5 Q45 9.5 43 15 Q44 21 40.5 24 Q36 27 31 24.5 Q27 21.5 27.6 15.5 Z"
          fill="#4a362a"
          opacity="0.92"
        />
        <!-- 眼睛：蓝杏仁眼（眨眼动画） -->
        <g class="cat-eyes">
          <ellipse cx="31.5" cy="16" rx="2.1" ry="2.6" fill="#4d7ca8" />
          <ellipse cx="40.5" cy="16" rx="2.1" ry="2.6" fill="#4d7ca8" />
          <circle cx="31.2" cy="15.2" r="0.55" fill="#eaf3fb" />
          <circle cx="40.2" cy="15.2" r="0.55" fill="#eaf3fb" />
        </g>
        <!-- 鼻子与嘴 -->
        <path d="M35.2 19.6 L37 19.6 L36.1 21 Z" fill="#c98a8a" />
        <path
          d="M36.1 21 Q34.6 22.6 33.4 21.8 M36.1 21 Q37.6 22.6 38.8 21.8"
          stroke="#4a362a"
          stroke-width="0.7"
          fill="none"
          stroke-linecap="round"
        />
        <!-- 胡须 -->
        <path
          d="M27 19.5 Q23 19 20 19.8 M27 21 Q23.4 21.4 21 22.4 M45 19.5 Q49 19 52 19.8 M45 21 Q48.6 21.4 51 22.4"
          stroke="#b7a687"
          stroke-width="0.6"
          fill="none"
          stroke-linecap="round"
        />
      </g>
    </svg>
  </button>
</template>

<script setup lang="ts">
defineProps<{ title?: string }>()
const emit = defineEmits<{ tap: [] }>()
</script>

<style>
/* 全局（非 scoped）：.reduce-motion 由 App 根节点挂载，需要跨组件覆盖。
   类名 cat- 前缀防冲突。 */

.siamese-cat {
  appearance: none;
  margin: 0;
  border: 0;
  background: transparent;
  padding: 0;
  color: inherit;
  cursor: pointer;
  /* 落在纸上的重量感：极轻暖墨落影 */
  filter: drop-shadow(0 1.5px 1px rgba(76, 54, 42, 0.2));
  transition: filter 300ms ease, transform 120ms ease;
}
.siamese-cat:active { transform: scale(.93); }
.siamese-cat:focus-visible { outline: 2px solid var(--fe-focus-ring); outline-offset: 2px; border-radius: 6px; }

.siamese-cat:hover {
  filter: drop-shadow(0 2.5px 2px rgba(76, 54, 42, 0.28));
}

.siamese-cat .cat-tail {
  transform-box: fill-box;
  transform-origin: 92% 92%;
  animation: cat-tail-sway 3.2s ease-in-out infinite;
}

.siamese-cat .cat-body {
  animation: cat-breathe 4s ease-in-out infinite;
  transform-box: fill-box;
  transform-origin: 50% 100%;
}

.siamese-cat .cat-head {
  animation: cat-head-bob 5.5s ease-in-out infinite;
  transform-box: fill-box;
  transform-origin: 50% 90%;
}

.siamese-cat .cat-eyes {
  animation: cat-blink 4.4s ease-in-out infinite;
  transform-box: fill-box;
  transform-origin: 50% 50%;
}

.siamese-cat .cat-ear-left {
  animation: cat-ear-twitch 7s ease-in-out infinite;
  transform-box: fill-box;
  transform-origin: 60% 90%;
}

.siamese-cat .cat-ear-right {
  animation: cat-ear-twitch 7s ease-in-out 3.2s infinite;
  transform-box: fill-box;
  transform-origin: 40% 90%;
}

/* hover：尾巴兴奋地快速摇 */
.siamese-cat:hover .cat-tail {
  animation-duration: 0.7s;
}

@keyframes cat-tail-sway {
  0%, 100% { transform: rotate(-9deg); }
  50% { transform: rotate(11deg); }
}

@keyframes cat-breathe {
  0%, 100% { transform: scaleY(1); }
  50% { transform: scaleY(1.035); }
}

@keyframes cat-head-bob {
  0%, 100% { transform: translateY(0) rotate(0deg); }
  30% { transform: translateY(-0.7px) rotate(-2.2deg); }
  65% { transform: translateY(0.4px) rotate(1.6deg); }
}

@keyframes cat-blink {
  0%, 94%, 100% { transform: scaleY(1); }
  96.5% { transform: scaleY(0.08); }
  98% { transform: scaleY(1); }
}

@keyframes cat-ear-twitch {
  0%, 88%, 100% { transform: rotate(0deg); }
  91% { transform: rotate(-7deg); }
  94% { transform: rotate(4deg); }
}

/* 无障碍：用户偏好减少动效时静止（App 根节点挂 reduce-motion 类） */
.reduce-motion .siamese-cat .cat-tail,
.reduce-motion .siamese-cat .cat-body,
.reduce-motion .siamese-cat .cat-head,
.reduce-motion .siamese-cat .cat-eyes,
.reduce-motion .siamese-cat .cat-ear-left,
.reduce-motion .siamese-cat .cat-ear-right {
  animation: none;
}
</style>
