# LexiFlow AI Landing Site

Static website for Razorpay onboarding and public product information.

## Deploy With Vercel Dashboard

1. Push this repository to GitHub.
2. In Vercel, choose **Add New Project**.
3. Import the repository.
4. Set **Root Directory** to `apps/landing`.
5. Leave build command empty.
6. Deploy.

## Deploy With Vercel CLI

Install Node.js first, then:

```powershell
npm i -g vercel
cd apps\landing
vercel
```

Use the deployed URL as the business website URL in Razorpay.

## Before Razorpay Submission

Replace placeholder business details if needed:

- `support@lexiflow.ai`
- business/legal name
- final pricing and currency wording
- any official company address or GST details if Razorpay asks for them

The landing site is public marketing/policy content only. The FastAPI backend
and Razorpay webhook still need separate production hosting before real payments.
