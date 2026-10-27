import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// These routes belonged to the old Kick/Patreon service and must not send
// users to that unrelated authentication backend in the YouTube dashboard.
export function middleware(request: NextRequest) {
  return NextResponse.redirect(new URL("/", request.url));
}

export const config = {
  matcher: ["/login", "/register", "/success", "/error"],
};
