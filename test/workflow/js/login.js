// 登录 admin 并挂载全局错误捕获
(async function () {
  // 1. 挂错误捕获
  if (!window.__errs) {
    window.__errs = [];
    window.addEventListener('error', function (e) {
      window.__errs.push({ type: 'error', msg: e.message, stack: e.error && e.error.stack, src: (e.filename || '') + ':' + e.lineno });
    });
    window.addEventListener('unhandledrejection', function (e) {
      window.__errs.push({ type: 'rejection', reason: String(e.reason), stack: e.reason && e.reason.stack });
    });
    var origErr = console.error.bind(console);
    console.error = function () {
      var args = [];
      for (var i = 0; i < arguments.length; i++) {
        var a = arguments[i];
        try { args.push(typeof a === 'object' ? JSON.stringify(a).slice(0, 400) : String(a).slice(0, 400)); } catch (ex) { args.push(String(a)); }
      }
      window.__errs.push({ type: 'console.error', args: args });
      origErr.apply(null, arguments);
    };
    var origWarn = console.warn.bind(console);
    console.warn = function () {
      var args = [];
      for (var i = 0; i < arguments.length; i++) {
        var a = arguments[i];
        try { args.push(typeof a === 'object' ? JSON.stringify(a).slice(0, 400) : String(a).slice(0, 400)); } catch (ex) { args.push(String(a)); }
      }
      if (String(args[0]).indexOf('[Vue warn]') === 0 || String(args.join(' ')).indexOf('Unhandled error') >= 0) {
        window.__errs.push({ type: 'vue.warn', args: args });
      }
      origWarn.apply(null, arguments);
    };
  }

  // 2. 已登录则跳过
  if (!document.querySelector('input[name=username]')) return { logged: 'already', url: location.href };

  // 3. 填表单
  function setVal(el, v) {
    var proto = el.type === 'password' ? HTMLInputElement.prototype : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
  var u = document.querySelector('input[name=username]');
  var p = document.querySelector('input[name=password]');
  setVal(u, 'admin');
  setVal(p, 'Admin@123');

  // 4. 点登录按钮（表单提交按钮）
  var btns = [...document.querySelectorAll('button')];
  var loginBtn = btns.find(function (b) { return b.textContent.indexOf('登录') >= 0 || b.type === 'submit'; });
  if (!loginBtn) return { error: '未找到登录按钮' };
  loginBtn.click();
  return { clicked: true };
})()
