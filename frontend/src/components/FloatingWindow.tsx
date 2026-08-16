import { createElement, type ElementType, type ReactNode, useEffect, useRef, useState } from 'react'

const EXIT_MS = 160

type FloatingWindowProps = {
  open: boolean
  children: ReactNode
  as?: ElementType
  className?: string
  role?: string
  'aria-label'?: string
}

export default function FloatingWindow({ open, children, as = 'div', className = '', role, 'aria-label': ariaLabel }: FloatingWindowProps) {
  const [mounted, setMounted] = useState(open)
  const [phase, setPhase] = useState<'enter' | 'exit'>(open ? 'enter' : 'exit')
  const contentRef = useRef<ReactNode>(children)
  if (open && children !== null) contentRef.current = children

  useEffect(() => {
    if (open) {
      setMounted(true)
      setPhase('enter')
      return
    }
    if (!mounted) return
    setPhase('exit')
    const timer = window.setTimeout(() => setMounted(false), EXIT_MS)
    return () => window.clearTimeout(timer)
  }, [mounted, open])

  if (!mounted) return null
  return createElement(as, { className: `floating-window floating-window--${phase} ${className}`.trim(), role, 'aria-label': ariaLabel }, open ? children : contentRef.current)
}
