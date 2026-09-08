// 挂载网络捕获 + 错误捕获（合并版）
(function () {
  // 网络捕获
  if (!window.__net) {
    window.__net = [];
    var origFetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : (input && input.url) || String(input);
      var method = (init && init.method) || (input && input.method) || 'GET';
      var entry = { url: String(url), method: method, status: null, error: null, at: Date.now() };
      window.__net.push(entry);
      return origFetch(input, init).then(function (resp) {
        entry.status = resp.status;
        // clone 读 body 不影响原流
        try {
          resp.clone().text().then(function (t) { entry.body = t.slice(0, 500); }).catch(function () {});
        } catch (e) {}
        return resp;
      }, function (err) {
        entry.error = String(err);
        return Promise.reject(err);
      });
    };
    var OrigXHR = window.XMLHttpRequest;
    function HookedXHR() {
      var xhr = new OrigXHR();
      var entry = { url: '', method: '', status: null, error: null, at: Date.now() };
      var origOpen = xhr.open.bind(xhr);
      xhr.open = function (method, url) {
        entry.method = method; entry.url = String(url);
        window.__net.push(entry);
        return origOpen.apply(null, arguments);
      };
      xhr.addEventListener('load', function () { entry.status = xhr.status; entry.body = String(xhr.responseText).slice(0, 500); });
      xhr.addEventListener('error', function () { entry.error = 'network-error'; });
      return xhr;
    }
    window.XMLHttpRequest = HookedXHR;
  }

  // 错误捕获
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
  }
  return 'hooked net+errs';
})()
