import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "../components/Layout";
import { LoginPage } from "../features/auth/LoginPage";
import { ProspectionNewPage } from "../features/prospection/ProspectionNewPage";
import { ProspectionRunPage } from "../features/prospection/ProspectionRunPage";
import { LeadsListPage } from "../features/leads/LeadsListPage";
import { LeadDetailPage } from "../features/leads/LeadDetailPage";
import { ProtectedRoute } from "../components/ProtectedRoute";
import { PipelinePage } from "../features/pipeline/PipelinePage";
import { LeadImportPage } from "../features/leads/LeadImportPage";
import { AuthProvider } from "../features/auth/useAuth";
import { AdminRoute } from "../features/admin/AdminRoute";
import { AdminDashboardPage } from "../features/admin/AdminDashboardPage";
import { TeamPage } from "../features/admin/TeamPage";
import { AssignmentsPage } from "../features/admin/AssignmentsPage";
import { SellerProfilePage } from "../features/sellerProfile/SellerProfilePage";
import { SalesPlaybookPage } from "../features/salesPlaybook/SalesPlaybookPage";
import { DemoLandingPage } from "../features/demo/DemoLandingPage";
import { DemoBriefPage } from "../features/demo/DemoBriefPage";
import { DemoProvider } from "../features/demo/DemoProvider";
import { DemoGate } from "../components/DemoGate";
import { useAuth } from "../features/auth/useAuth";

function PublicHome() {
  const { isAuthenticated, isLoading, demo } = useAuth();
  if (isLoading) return <div className="min-h-screen" role="status">Cargando…</div>;
  if (isAuthenticated) return <Navigate to={demo?.enabled ? "/demo" : "/leads"} replace />;
  return <DemoLandingPage />;
}

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<PublicHome />} />
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <ProtectedRoute>
              <DemoProvider><Layout /></DemoProvider>
            </ProtectedRoute>
          }
        >
          <Route path="/demo" element={<DemoGate><DemoBriefPage /></DemoGate>} />
          <Route path="/prospection/new" element={<DemoGate><ProspectionNewPage /></DemoGate>} />
          <Route path="/prospection/:runId" element={<DemoGate><ProspectionRunPage /></DemoGate>} />
          <Route path="/leads" element={<DemoGate><LeadsListPage /></DemoGate>} />
          <Route path="/leads/import" element={<DemoGate internalOnly><LeadImportPage /></DemoGate>} />
          <Route path="/leads/:leadId" element={<DemoGate><LeadDetailPage /></DemoGate>} />
          <Route path="/pipeline" element={<DemoGate internalOnly><PipelinePage /></DemoGate>} />
          <Route path="/seller-profile" element={<DemoGate><SellerProfilePage /></DemoGate>} />
          <Route path="/sales-playbook" element={<DemoGate><SalesPlaybookPage /></DemoGate>} />
          <Route path="/admin" element={<DemoGate internalOnly><AdminRoute><AdminDashboardPage /></AdminRoute></DemoGate>} />
          <Route path="/admin/team" element={<DemoGate internalOnly><AdminRoute><TeamPage /></AdminRoute></DemoGate>} />
          <Route path="/admin/assignments" element={<DemoGate internalOnly><AdminRoute><AssignmentsPage /></AdminRoute></DemoGate>} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}

export default App;
