# Trustpilot Enrichment Web UI

Simple React frontend for uploading Trustpilot CSV files and downloading enriched results.

## Features

- 📤 CSV file upload with validation
- ⚙️ Optional lender name override
- 📊 Real-time status updates
- 📥 Automatic download of enriched CSV
- 🎨 Clean, modern UI
- ⚡ Built with React + Vite

## Prerequisites

- Node.js 18+ installed
- Backend API running (see main README.md)

## Development Setup

### 1. Install Dependencies

```bash
cd web
npm install
```

### 2. Configure API URL

The app uses the API URL from environment variables or defaults to `http://localhost:8000`.

For local development, the default works if your API server is running on port 8000.

For production, create `.env.production`:

```bash
VITE_API_BASE_URL=https://your-api-domain.com
```

### 3. Run Development Server

```bash
npm run dev
```

The app will be available at `http://localhost:3000`

## Building for Production

### Build Static Assets

```bash
npm run build
```

This creates optimized static files in the `dist/` directory.

### Preview Production Build

```bash
npm run preview
```

## Deployment

The frontend is a static site that can be deployed to any static hosting service.

### Option 1: Netlify

1. Connect your GitHub repository
2. Set build command: `cd web && npm run build`
3. Set publish directory: `web/dist`
4. Add environment variable: `VITE_API_BASE_URL=https://your-api.com`
5. Deploy

Or use Netlify CLI:
```bash
cd web
npm run build
netlify deploy --prod --dir=dist
```

### Option 2: Vercel

1. Connect GitHub repository
2. Set root directory: `web`
3. Framework preset: Vite
4. Add environment variable: `VITE_API_BASE_URL`
5. Deploy

Or use Vercel CLI:
```bash
cd web
npm run build
vercel --prod
```

### Option 3: Cloudflare Pages

1. Connect GitHub repository
2. Build command: `npm run build`
3. Build output directory: `dist`
4. Root directory: `web`
5. Environment variable: `VITE_API_BASE_URL`

### Option 4: AWS S3 + CloudFront

```bash
cd web
npm run build

# Upload to S3
aws s3 sync dist/ s3://your-bucket-name/

# Invalidate CloudFront cache
aws cloudfront create-invalidation --distribution-id YOUR_DIST_ID --paths "/*"
```

### Option 5: GitHub Pages

```bash
cd web
npm run build

# Deploy to gh-pages branch
npx gh-pages -d dist
```

## Configuration

### API URL Configuration

The app determines the API URL in this order:

1. `VITE_API_BASE_URL` environment variable (highest priority)
2. Default: `http://localhost:8000` (for development)

#### Setting API URL for Different Environments

**Development (.env.development):**
```bash
VITE_API_BASE_URL=http://localhost:8000
```

**Production (.env.production):**
```bash
VITE_API_BASE_URL=https://api.yourdomain.com
```

**Build-time override:**
```bash
VITE_API_BASE_URL=https://api.example.com npm run build
```

## Usage

1. Open the web app in your browser
2. Click "Choose File" and select your Trustpilot CSV
3. (Optional) Enter a lender name override
4. Click "Run Enrichment"
5. Wait for processing (status updates shown)
6. Enriched CSV downloads automatically when complete

## File Structure

```
web/
├── src/
│   ├── App.jsx         # Main application component
│   ├── App.css         # Application styles
│   ├── main.jsx        # React entry point
│   └── config.js       # API configuration
├── index.html          # HTML entry point
├── package.json        # Dependencies and scripts
├── vite.config.js      # Vite configuration
└── README.md           # This file
```

## CORS Configuration

The backend API must allow requests from your frontend domain.

Set the `FRONTEND_ORIGIN` environment variable in your backend:

```bash
# For single origin
FRONTEND_ORIGIN=https://your-frontend.netlify.app

# For multiple origins
FRONTEND_ORIGIN=https://your-frontend.netlify.app,https://your-frontend.vercel.app

# For development (allow all)
FRONTEND_ORIGIN=*
```

## Troubleshooting

### "Failed to fetch" error

- Check that API server is running
- Verify API URL in `src/config.js`
- Check browser console for CORS errors
- Ensure backend has correct CORS configuration

### File upload not working

- Ensure file is .csv format
- Check file size (large files may timeout)
- Verify backend API is accessible

### Build errors

```bash
# Clear node_modules and reinstall
rm -rf node_modules package-lock.json
npm install
```

### Environment variables not working

- Ensure variables start with `VITE_`
- Restart dev server after changing .env files
- For production, set variables in deployment platform

## Development Tips

### Hot Module Replacement

Vite provides instant HMR - changes appear immediately without full reload.

### API Testing

Test API separately:
```bash
curl http://localhost:8000/health
```

### Component Development

The app is a single component (`App.jsx`) for simplicity. For larger apps, consider splitting into:
- `FileUpload.jsx`
- `StatusDisplay.jsx`
- `InfoSection.jsx`

## Security Notes

1. **API Keys**: Never expose API keys in frontend code
2. **File Size**: Browser has limits on file size/memory
3. **HTTPS**: Always use HTTPS in production
4. **CORS**: Restrict origins in production backend

## Performance

- Initial load: ~100KB (gzipped)
- Fast static serving
- API calls made only on enrichment

## Browser Support

- Chrome/Edge 90+
- Firefox 88+
- Safari 14+
- Modern mobile browsers

## License

Same as main project.

---

For backend deployment, see `../DEPLOY.md`
