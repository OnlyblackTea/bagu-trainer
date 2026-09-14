/** 骨架 smoke：外壳能渲染。真实屏幕的用例在各自组件的测试文件里。 */
import { render, screen } from '@testing-library/react'

import App from '../App'

describe('App shell', () => {
  it('renders the header', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: '八股实战演练所' })).toBeInTheDocument()
  })

  it('states the platform gate+指示 stance', () => {
    render(<App />)
    expect(screen.getByText(/gate \+ 指示/)).toBeInTheDocument()
  })
})
