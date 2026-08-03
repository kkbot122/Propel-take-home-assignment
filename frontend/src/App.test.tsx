import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { App } from './App'

describe('App', () => {
  it('renders the foundation placeholder', () => {
    render(<App />)

    expect(
      screen.getByRole('heading', { name: 'Propel operator console' }),
    ).toBeInTheDocument()
    expect(screen.getByText('VS-01 · Foundation')).toBeInTheDocument()
  })
})
