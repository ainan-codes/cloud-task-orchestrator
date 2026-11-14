import React from "react";

/**
 * BrandIcons — Official vector brand marks for multi-platform distribution.
 * Pixel-perfect SVG paths matching Meta, Snapchat, YouTube, Rumble, Dailymotion, and Google Drive.
 */

export function FacebookIcon({ size = 18, className = "", style = {} }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ flexShrink: 0, ...style }}
    >
      <circle cx="12" cy="12" r="12" fill="#0866FF" />
      <path
        d="M13.8 21.84V14.5h2.46l.37-2.86H13.8V9.82c0-.83.23-1.4 1.42-1.4h1.52V5.86c-.26-.04-1.17-.11-2.22-.11-2.2 0-3.7 1.34-3.7 3.8v2.09H8.4V14.5h2.42v7.34c.76.12 1.54.16 2.33.16.8 0 1.57-.04 2.33-.16z"
        fill="#FFFFFF"
      />
    </svg>
  );
}

export function SnapchatIcon({ size = 18, className = "", style = {} }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ flexShrink: 0, ...style }}
    >
      <rect width="24" height="24" rx="5.5" fill="#FFFC00" />
      <path
        d="M12.003 4.2c-2.378 0-4.088 1.776-4.088 3.96 0 .426.103.978.188 1.433-.61.127-1.37.457-1.542 1.137-.095.372.106.666.388.752.127.039.247.054.347.073-.109.395-.44 1.144-.888 1.258-.29.074-.593.111-.79.377-.183.248-.168.618.175.845.545.36 1.341.43 1.954.58.337.669.882 1.213 1.579 1.53-.418.232-1.079.43-1.748.65-.436.143-.728.468-.707.876.02.404.354.717.828.777.94.12 1.986-.068 2.827-.478.533-.26 1.05-.285 1.548-.285.5 0 1.018.026 1.55.285.84.41 1.887.598 2.827.478.474-.06.808-.373.828-.777.02-.408-.271-.733-.707-.876-.669-.22-1.33-.418-1.748-.65.697-.317 1.242-.861 1.579-1.53.613-.15 1.409-.22 1.954-.58.343-.227.358-.597.175-.845-.197-.266-.5-.303-.79-.377-.448-.114-.779-.863-.888-1.258.1-.019.22-.034.347-.073.282-.086.483-.38.388-.752-.172-.68-.932-1.01-1.542-1.137.085-.455.188-1.007.188-1.433 0-2.184-1.71-3.96-4.088-3.96z"
        fill="#FFFFFF"
        stroke="#000000"
        strokeWidth="0.85"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function YouTubeIcon({ size = 18, className = "", style = {} }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ flexShrink: 0, ...style }}
    >
      <path
        d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814z"
        fill="#FF0000"
      />
      <polygon points="9.545,15.568 15.818,12 9.545,8.432" fill="#FFFFFF" />
    </svg>
  );
}

export function RumbleIcon({ size = 18, className = "", style = {} }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ flexShrink: 0, ...style }}
    >
      <rect width="24" height="24" rx="5.5" fill="#85C742" />
      <path
        d="M10.1 5.5c-1.12.02-2.18.52-2.91 1.39L4.85 9.77c-1.11 1.33-1.11 3.26 0 4.59l2.34 2.8c.73.87 1.79 1.37 2.91 1.39h3.8c1.12-.02 2.18-.52 2.91-1.39l2.34-2.8c1.11-1.33 1.11-3.26 0-4.59L16.8 6.89c-.73-.87-1.79-1.37-2.91-1.39h-3.79zm1.9 3.5l4 3-4 3V9z"
        fill="#FFFFFF"
      />
    </svg>
  );
}

export function DailymotionIcon({ size = 18, className = "", style = {} }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ flexShrink: 0, ...style }}
    >
      <rect width="24" height="24" rx="5.5" fill="#0066DC" />
      <path
        d="M13.8 4.2v6.1c-.8-.9-1.9-1.4-3.2-1.4-2.9 0-5.1 2.3-5.1 5.5s2.2 5.4 5.1 5.4c1.3 0 2.4-.5 3.2-1.4v1.2h3V4.2h-3zm-2.5 13c-1.6 0-2.8-1.2-2.8-2.8s1.2-2.8 2.8-2.8 2.8 1.2 2.8 2.8-1.2 2.8-2.8 2.8z"
        fill="#FFFFFF"
      />
    </svg>
  );
}

export function GoogleDriveIcon({ size = 18, className = "", style = {} }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 87.3 78"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ flexShrink: 0, ...style }}
    >
      <path d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z" fill="#0066da"/>
      <path d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0 -1.2 4.5h27.5z" fill="#00ac47"/>
      <path d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z" fill="#ea4335"/>
      <path d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z" fill="#00832d"/>
      <path d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z" fill="#2684fc"/>
      <path d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z" fill="#ffba00"/>
    </svg>
  );
}

export function MetaIcon({ size = 18, className = "", style = {} }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ flexShrink: 0, ...style }}
    >
      <path
        d="M16.5 6c-1.8 0-3.3.9-4.5 2.4C10.8 6.9 9.3 6 7.5 6 4.5 6 2 8.5 2 12s2.5 6 5.5 6c1.8 0 3.3-.9 4.5-2.4 1.2 1.5 2.7 2.4 4.5 2.4 3 0 5.5-2.5 5.5-6s-2.5-6-5.5-6zm-9 9.8c-2 0-3.6-1.7-3.6-3.8s1.6-3.8 3.6-3.8c1.3 0 2.5.7 3.2 1.8-.8 1.2-1.8 2.8-2.6 4.4-.2.9-.4 1.4-.6 1.4zm9 0c-.2 0-.4-.5-.6-1.4-.8-1.6-1.8-3.2-2.6-4.4.7-1.1 1.9-1.8 3.2-1.8 2 0 3.6 1.7 3.6 3.8s-1.6 3.8-3.6 3.8z"
        fill="#0081FB"
      />
    </svg>
  );
}
