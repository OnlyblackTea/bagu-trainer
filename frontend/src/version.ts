/**
 * 前端**期望**的平台版本（后端 `GET /meta` 里的 `platform_version`）。
 *
 * 为什么需要它：自托管下"前端与后端不是同一次部署"是常态而不是意外——拉了新代码但忘了重启后端，
 * 或者反向代理指着另一台机器。症状极难自查：某个新接口 404（Starlette 的原生
 * `{"detail":"Not Found"}`，一个不在我们短码表里的字符串），或者响应少一个字段。
 *
 * 于是主页把 `/meta` 的 `platform_version` 和这里比：不一致就直接说"后端比前端旧/新，重新部署并
 * 重启后端"，而不是让学员对着一个沉默的 404 猜。
 *
 * 两边一致由 `tests/test_frontend_contract.py` 守着（它读这个常量、和后端的 `PLATFORM_VERSION`
 * 比），所以升版本时不会只升一边。
 */
export const EXPECTED_PLATFORM_VERSION = 'a-prime-0.1.0'
