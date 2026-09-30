import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import { SidebarProvider, useSidebar } from '../contexts/SidebarContext';

function LayoutContent() {
  const { sidebarWidth } = useSidebar();

  return (
    <>
      <Sidebar />
      <main
        className="min-h-screen transition-all duration-300"
        style={{
          marginLeft: `${sidebarWidth}px`,
          width: `calc(100% - ${sidebarWidth}px)`
        }}
      >
        <div className="w-full py-6 px-4 sm:px-6 lg:px-8">
          <Outlet />
        </div>
      </main>
    </>
  );
}

export default function Layout() {
  return (
    <SidebarProvider>
      <div className="min-h-screen bg-gray-50">
        <LayoutContent />
      </div>
    </SidebarProvider>
  );
}
