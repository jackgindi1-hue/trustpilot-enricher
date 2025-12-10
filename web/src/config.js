/**
 * Configuration for API connection
 *
 * For local development: Use localhost:8000
 * For production: Set your deployed API URL
 */

const config = {
  // Default to localhost for development
  // Override with VITE_API_BASE_URL environment variable
  API_BASE_URL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
}

export default config
