// 挂载全局错误捕获（独立于登录）
(function () {
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
        try { args.push(typeof a === 'object' ? JSON.stringify(a).slice(0, 600) : String(a).slice(0, 600)); } catch (ex) { args.push(String(a)); }
      }
      window.__errs.push({ type: 'console.error', args: args });
      origErr.apply(null, arguments);
    };
    var origWarn = console.warn.bind(console);
    console.warn = function () {
      var args = [];
      for (var i = 0; i < arguments.length; i++) {
        var a = arguments[i];
        try { args.push(typeof a === 'object' ? JSON.stringify(a).slice(0, 600) : String(a).slice(0, 600)); } catch (ex) { args.push(String(a)); }
      }
      if (String(args.join(' ')).indexOf('Vue warn') >= 0 || String(args.join(' ')).indexOf('Unhandled error') >= 0) {
        window.__errs.push({ type: 'vue.warn', args: args });
      }
      origWarn.apply(null, arguments);
    };
    return 'hooked';
  }
  return 'already';
})()
