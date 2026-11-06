import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <div className="flex justify-center">
      {/* Land on the profiles list; an explicit redirect_url (where you were sent from) still wins. */}
      <SignIn fallbackRedirectUrl="/profiles" signUpFallbackRedirectUrl="/profiles" />
    </div>
  );
}
