import { ArrowLeft } from "lucide-react";

export default function BackToHome() {
  return (
    <a href="#home" className="back-to-home">
      <ArrowLeft size={16} aria-hidden="true" />
      Back to home
    </a>
  );
}
