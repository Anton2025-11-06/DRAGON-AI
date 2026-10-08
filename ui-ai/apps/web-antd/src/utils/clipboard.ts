/**
 * 复制到剪贴板。
 *
 * 为什么要自己写一层：navigator.clipboard 只在 https / localhost 下存在，
 * 内网 http 部署（本项目常见）直接访问会拿到 undefined 或抛错，按钮点了没反应。
 * 这里在不可用时退回 textarea + execCommand，行为对调用方一致。
 *
 * 只返回成功与否、不弹提示：提示文案各页不同（有的要带「请手动复制」），
 * 由调用方决定，避免这个工具函数到处塞 message。
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false;

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 继续走兜底路径，不直接判定失败
  }

  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    // readonly 防止移动端自动拉起键盘；离屏定位避免页面闪一下
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    textarea.style.opacity = '0';
    document.body.append(textarea);
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    const ok = document.execCommand('copy');
    textarea.remove();
    return ok;
  } catch {
    return false;
  }
}
