// `/vitest` 入口才会把 jest-dom 的匹配器类型挂到 vitest 的 expect 上；
// 用根入口时运行时能用、但 `tsc` 不认 toBeInTheDocument。
import '@testing-library/jest-dom/vitest'
