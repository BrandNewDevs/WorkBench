import { ArrowLeft } from "lucide-react";
import { IoHome } from "react-icons/io5";

export default function BackToHome() {
  return (
    <a href="#home" className="back-to-home" aria-label="Back to home">
      <ArrowLeft size={16} aria-hidden="true" />
      <IoHome size={15} aria-hidden="true" />
    </a>
  );
}
