---
paths:
  - "dashboard/**/*"
---
# Frontend Patterns

> From ECC frontend-patterns skill. Applicable to `dashboard/` (React 19 + Vite 8 + Tailwind 4).

## Component Patterns

### Composition Over Inheritance

```tsx
interface CardProps {
  children: React.ReactNode
  variant?: 'default' | 'outlined'
}

export function Card({ children, variant = 'default' }: CardProps) {
  return <div className={`card card-${variant}`}>{children}</div>
}

export function CardHeader({ children }: { children: React.ReactNode }) {
  return <div className="card-header">{children}</div>
}

export function CardBody({ children }: { children: React.ReactNode }) {
  return <div className="card-body">{children}</div>
}
```

### Custom Hooks

```tsx
export function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value)

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value)
    }, delay)

    return () => clearTimeout(handler)
  }, [value, delay])

  return debouncedValue
}
```

## Performance Optimization

### Memoization

```tsx
// useMemo for expensive computations
const sortedItems = useMemo(() => {
  return items.sort((a, b) => b.volume - a.volume)
}, [items])

// useCallback for functions passed to children
const handleSearch = useCallback((query: string) => {
  setSearchQuery(query)
}, [])

// React.memo for pure components
export const ItemCard = React.memo<ItemCardProps>(({ item }) => {
  return <div>{item.name}</div>
})
```

### Code Splitting & Lazy Loading

```tsx
import { lazy, Suspense } from 'react'

const HeavyChart = lazy(() => import('./HeavyChart'))

export function Dashboard() {
  return (
    <Suspense fallback={<ChartSkeleton />}>
      <HeavyChart data={data} />
    </Suspense>
  )
}
```

## State Management

### Context + Reducer Pattern

```tsx
interface State {
  items: Item[]
  loading: boolean
}

type Action =
  | { type: 'SET_ITEMS'; payload: Item[] }
  | { type: 'SET_LOADING'; payload: boolean }

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'SET_ITEMS':
      return { ...state, items: action.payload }
    case 'SET_LOADING':
      return { ...state, loading: action.payload }
    default:
      return state
  }
}
```

## Error Boundary

```tsx
export class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: Error | null }
> {
  state = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-fallback">
          <h2>Something went wrong</h2>
          <p>{this.state.error?.message}</p>
          <button onClick={() => this.setState({ hasError: false })}>
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
```

## Dashboard Commands

```bash
# Dev server
cd dashboard && npm run dev

# Build
cd dashboard && npm run build

# Lint
cd dashboard && npm run lint
```
