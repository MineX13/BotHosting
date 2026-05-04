import axios from 'axios'

// When hosting frontend on Vercel, set VITE_API_URL to your VPS backend URL
// e.g. VITE_API_URL=https://your-vps-ip:8080/api
const API_BASE = import.meta.env.VITE_API_URL || '/api'

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
})

// Add JWT token to every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Handle 401 responses globally
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('username')
      localStorage.removeItem('avatar')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export default api
