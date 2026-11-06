import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

const isPublicRoute = createRouteMatcher(["/sign-in(.*)", "/sign-up(.*)"]);

export default clerkMiddleware(async (auth, req) => {
  // "/" has no page of its own. Redirect here rather than with redirect() in app/page.tsx: Clerk lands
  // on "/" with a client-side navigation after sign-in, and that server redirect isn't followed
  // there, leaving a blank page.
  if (req.nextUrl.pathname === "/") return NextResponse.redirect(new URL("/profiles", req.url));
  if (!isPublicRoute(req)) await auth.protect();
});

export const config = {
  matcher: [
    // Skip Next.js internals and static files.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
