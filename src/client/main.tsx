import { createRoot } from 'react-dom/client'
import { App } from './App.js'
import './styles.css'

const root = document.querySelector('#root')

if (!(root instanceof HTMLElement)) {
  throw new Error('GFIT CoWork could not find its application root.')
}

createRoot(root).render(<App />)
