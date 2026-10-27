import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { Navigate } from "react-router-dom";

export function DemoLandingPage() {
  const { isAuthenticated, demo } = useAuth();
  if (isAuthenticated) return <Navigate to={demo?.enabled ? "/demo" : "/leads"} replace />;
  return <main className="demo-landing">
    <nav className="demo-topbar" aria-label="Principal"><Link className="demo-brand" to="/" aria-label="Tu Web MX, inicio"><span className="demo-mark">T</span><span>Tu Web MX</span></Link><Link to="/login" className="demo-top-link">Entrar a la demo</Link></nav>
    <section className="demo-hero">
      <div className="demo-copy">
        <p className="demo-kicker">Prospección con contexto</p>
        <h1>Encuentra negocios.<br /><span>Entiende qué sabes.</span></h1>
        <p className="demo-intro">Tu Web MX reúne negocios de Maps, evidencia pública y tus condiciones comerciales para preparar un primer contacto que una persona pueda revisar.</p>
        <Link className="demo-cta" to="/login">Probar demo <span aria-hidden="true">↗</span></Link>
        <p className="demo-limit">2 búsquedas · hasta 3 negocios por búsqueda · sin envíos automáticos</p>
      </div>
      <div className="demo-visual" aria-label="Vista ilustrativa de negocios en un mapa y sus evidencias">
        <div className="demo-map-grid" aria-hidden="true"><span className="map-street street-a" /><span className="map-street street-b" /><span className="map-street street-c" /><span className="map-street street-d" /><span className="map-park" /></div>
        <span className="map-pin pin-one">01</span><span className="map-pin pin-two">02</span><span className="map-pin pin-three">03</span>
        <article className="demo-evidence"><div className="evidence-top"><span className="evidence-dot" /> Negocio de ejemplo</div><h2>Clínica Roma</h2><p>Presencia local encontrada</p><div className="evidence-rule" /><div className="evidence-source"><span>↗</span><span><b>Ficha de Maps</b><small>Identidad y contacto atribuibles</small></span></div><div className="evidence-source muted"><span>?</span><span><b>Sitio propio</b><small>Aún no confirmado</small></span></div></article>
        <span className="visual-caption">Una señal observada no confirma intención de compra.</span>
      </div>
    </section>
    <section className="demo-steps" aria-label="Cómo funciona"><div><span>Revisa</span><p>Ajusta el ejemplo comercial y aprueba tus condiciones.</p></div><div><span>Busca</span><p>Encuentra hasta tres negocios en un área de Maps.</p></div><div><span>Prepara</span><p>Consulta evidencia y crea un borrador pendiente de revisión.</p></div></section>
    <footer className="demo-footer"><span>Tu Web MX</span><span>La evidencia orienta la conversación. No predice resultados.</span></footer>
  </main>;
}
