import { createContext, useContext, useState, useEffect } from 'react'

// eslint-disable-next-line react-refresh/only-export-components
export const PALETTES = [
  { id: 'stone',    label: '暖石' },
  { id: 'lavender', label: '薰衣草' },
  { id: 'sage',     label: '鼠尾草' },
  { id: 'blue',     label: '蓝色' },
]

const ThemeContext = createContext(null)

function resolveTheme(raw) {
  // migrate old 'dark'/'light' values
  if (raw === 'dark' || raw === 'light') return `stone-${raw}`
  return raw || 'stone-dark'
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() =>
    resolveTheme(localStorage.getItem('theme'))
  )

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  const palette = theme.split('-')[0]
  const mode    = theme.split('-')[1] || 'dark'

  const setTheme = (t) => setThemeState(t)

  const toggle = () =>
    setThemeState(`${palette}-${mode === 'dark' ? 'light' : 'dark'}`)

  const setPalette = (p) =>
    setThemeState(`${p}-${mode}`)

  return (
    <ThemeContext.Provider value={{ theme, palette, mode, setTheme, toggle, setPalette }}>
      {children}
    </ThemeContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export const useTheme = () => useContext(ThemeContext)
