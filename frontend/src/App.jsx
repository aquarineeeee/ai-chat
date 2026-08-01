import { AuthProvider, useAuth } from './AuthContext'
import { ThemeProvider } from './ThemeContext'
import LoginPage from './LoginPage'
import ChatPage from './ChatPage'
import ColorPreview from './ColorPreview'
import './App.css'

function AppInner() {
  const { user, loading } = useAuth()

  if (window.location.pathname === '/color-preview') return <ColorPreview />

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg-base)' }}>
        <div className="w-8 h-8 rounded-full animate-spin" style={{ border: '2px solid var(--accent)', borderTopColor: 'transparent' }} />
      </div>
    )
  }

  return user ? <ChatPage /> : <LoginPage />
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <AppInner />
      </AuthProvider>
    </ThemeProvider>
  )
}
