import axios from "axios";
import { ElMessage } from "element-plus";

// 平台后端（8000），通过 vite 代理，无跨域问题
const http = axios.create({ baseURL: "/api", timeout: 60000 });

// AI 助手（5001）
const aiHttp = axios.create({ baseURL: "/ai", timeout: 180000 });

/** 统一解包 {code,message,data} 结构；code!==0 一律当错误抛出。 */
function unwrap(response) {
  const body = response.data;
  if (body && typeof body === "object" && "code" in body) {
    if (body.code === 0) return body.data;
    const err = new Error(body.message || "请求失败");
    err.code = body.code;
    err.payload = body;
    throw err;
  }
  return body;
}

function makeInterceptors(instance) {
  instance.interceptors.response.use(
    unwrap,
    (error) => {
      const res = error.response;

      // 428：危险操作需要二次确认。交给调用方弹窗，不在这里报错提示。
      if (res && res.status === 428) {
        const err = new Error((res.data && res.data.message) || "需要二次确认");
        err.code = 428;
        err.needConfirm = true;
        err.payload = res.data;
        return Promise.reject(err);
      }

      let msg = "网络异常，请检查后端服务是否已启动";
      if (res) {
        msg = (res.data && (res.data.message || res.data.error)) || `请求失败（HTTP ${res.status}）`;
      } else if (error.code === "ECONNABORTED") {
        msg = "请求超时";
      }
      ElMessage.error(msg);
      const err = new Error(msg);
      err.code = res ? res.status : 0;
      return Promise.reject(err);
    }
  );
}
makeInterceptors(http);
makeInterceptors(aiHttp);

export default http;
export { aiHttp };
