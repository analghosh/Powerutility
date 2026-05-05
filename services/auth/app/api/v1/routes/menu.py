"""
Menu API routes - Only required endpoints
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from libs.core.authz import get_current_user, UserPrincipal
from ....repository.menu_repository import MenuRepository
from ....core.logger import get_auth_logger
from ....core.dto import AuthUserDTO
from ....models.menu import MenuCreate, MenuUpdate, MenuInDB

logger = get_auth_logger(__name__)

router = APIRouter(prefix="/menu", tags=["Menu Management"])

# Initialize repository
menu_repository = MenuRepository()


@router.get("/", response_model=List[Dict[str, Any]])
async def get_assigned_menus(
    current_user: UserPrincipal = Depends(get_current_user)
):
    """
    Get assigned menus for the current user based on their roles and permissions
    
    Returns:
        Hierarchical menu tree with nested children
    """
    try:
        logger.info(f"Getting assigned menus for user: {current_user.user_id}")
        logger.info(f"Current user object: {current_user}")
        
        # Get user permissions from centralized system
        user_permissions = current_user.effective_permissions
        
        logger.info(f"User permissions: {user_permissions}")
        
        # Check if user has admin access (all menus)
        if current_user.is_super_admin or "*" in user_permissions:
            logger.info("Admin user - returning complete menu tree")
            menu_tree = await menu_repository.get_menu_tree()
            
            if not menu_tree:
                logger.warning("No menu tree returned from repository")
                return []
            
            logger.info(f"Successfully retrieved menu tree with {len(menu_tree)} root menus")
            result = [menu.model_dump() for menu in menu_tree]
            logger.info(f"Returning {len(result)} menu items")
            return result
        
        # For non-admin users, get menus based on their roles (more reliable than permission parsing)
        logger.info(f"Non-admin user - getting role-based menus")
        
        from ....repository.menu_role_repository import MenuRoleRepository
        from ....repository.role_repository import RoleRepository
        from libs.core.dependencies import get_db
        
        # Get user roles from UserPrincipal (more reliable than database lookup)
        # The UserPrincipal should already have the user's roles
        user_roles = getattr(current_user, 'roles', [])
        
        # If roles not available in UserPrincipal, get from database
        if not user_roles:
            try:
                from ....repository.user_repository import UserRepository
                user_repo = UserRepository() # UserRepository methods are now async
                user = user_repo.get_user_by_id(current_user.user_id)
                if user:
                    user_roles = user.roles
                else:
                    logger.warning(f"User not found in database: {current_user.user_id}")
                    return []
            except Exception as e:
                logger.error(f"Error getting user from database: {e}")
                return []
        logger.info(f"User roles: {user_roles}")
        
        menu_role_repo = MenuRoleRepository()
        role_repo = RoleRepository() # RoleRepository methods are not async, so no change needed here.
        
        # Get all accessible menu IDs for the user's roles
        accessible_menu_ids = set()
        
        for role_name in user_roles:
            role = await role_repo.get_role_by_name(role_name)
            if role:
                # Get menus assigned to this role
                role_menus = await menu_role_repo.get_menus_for_role(role.id)
                accessible_menu_ids.update(role_menus)
        
        logger.info(f"User has access to {len(accessible_menu_ids)} menu IDs: {accessible_menu_ids}")
        
        if not accessible_menu_ids:
            logger.info("No menus assigned to user roles")
            return []
        
        # Get the complete menu tree and filter by accessible menus
        all_menus = await menu_repository.get_all_active_menus()
        accessible_menus = [menu for menu in all_menus if menu.id in accessible_menu_ids]
        
        # Build hierarchical menu tree for accessible menus
        menu_tree = await menu_repository.build_menu_tree(accessible_menus)
        
        logger.info(f"Successfully retrieved {len(menu_tree)} accessible root menus")
        result = [menu.model_dump() for menu in menu_tree]
        logger.info(f"Returning {len(result)} menu items")
        return result
        
    except Exception as e:
        logger.error(f"Error getting assigned menus: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve assigned menus: {str(e)}")


@router.get("/test", response_model=List[Dict[str, Any]])
async def test_menu_tree():
    """Test endpoint to check if menu tree building works"""
    try:
        logger.info("Testing menu tree building...")
        menu_tree = await menu_repository.get_menu_tree()
        
        if not menu_tree:
            logger.warning("No menu tree returned from repository")
            return []
        
        logger.info(f"Successfully retrieved menu tree with {len(menu_tree)} root menus")
        result = [menu.model_dump() for menu in menu_tree]
        logger.info(f"Returning {len(result)} menu items")
        return result
        
    except Exception as e:
        logger.error(f"Error in test menu tree: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Test failed: {str(e)}")

@router.get("/list", response_model=List[Dict[str, Any]])
async def get_all_menus_for_admin(
    current_user: UserPrincipal = Depends(get_current_user)
):
    """
    Get all menus for admin management - used for menu assignment interface
    
    Returns:
        Flat list of all menu items in the system
    """
    try:
        logger.info(f"Admin getting all menus for assignment: {current_user.user_id}")
        
        # Verify admin permissions (centralized system handles this automatically)
        if not current_user.is_super_admin and "*" not in current_user.effective_permissions:
            logger.warning(f"Non-admin user {current_user.user_id} attempted to access admin menu list")
            raise HTTPException(status_code=403, detail="Admin access required")
        
        # Get all menus (flat list)
        all_menus = await menu_repository.get_all_active_menus()
        
        logger.info(f"Admin retrieved {len(all_menus)} total menus for assignment")
        return [menu.model_dump() for menu in all_menus]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting admin menu list: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve admin menu list")


@router.post("/", response_model=MenuInDB)
async def create_menu(
    menu_data: MenuCreate,
    current_user: UserPrincipal = Depends(get_current_user)
):
    """
    Create a new menu item
    
    Args:
        menu_data: Menu creation data
        current_user: Current authenticated user (must have Administration:create permission)
        
    Returns:
        Created menu item
    """
    try:
        logger.info(f"Creating new menu: {menu_data.name} by user: {current_user.username}")
        
        # Verify admin permissions
        user_permissions = current_user.permissions
        user_roles = current_user.roles
        
        if "*" not in user_permissions and "admin" not in user_roles:
            logger.warning(f"Non-admin user {current_user.username} attempted to create menu")
            raise HTTPException(status_code=403, detail="Admin access required for menu creation")
        
        # Check if parent menu exists (if parent_id is provided)
        if menu_data.parent_id:
            parent_menu = await menu_repository.get_menu_by_id(menu_data.parent_id)
            if not parent_menu:
                raise HTTPException(status_code=400, detail=f"Parent menu with ID {menu_data.parent_id} not found")
        
        # Create the menu
        created_menu = await menu_repository.create_menu(menu_data)
        
        logger.info(f"Successfully created menu: {created_menu.name} with ID: {created_menu.id}")
        return created_menu
        
    except ValueError as e:
        logger.warning(f"Menu creation failed - validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating menu: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create menu: {str(e)}")


@router.put("/{menu_id}", response_model=MenuInDB)
async def update_menu(
    menu_id: str,
    menu_data: MenuUpdate,
    current_user: UserPrincipal = Depends(get_current_user)
):
    """
    Update an existing menu item
    
    Args:
        menu_id: ID of the menu to update
        menu_data: Menu update data
        current_user: Current authenticated user (must have Administration:write permission)
        
    Returns:
        Updated menu item
    """
    try:
        logger.info(f"Updating menu ID: {menu_id} by user: {current_user.username}")
        
        # Verify admin permissions
        user_permissions = current_user.permissions
        user_roles = current_user.roles
        
        if "*" not in user_permissions and "admin" not in user_roles:
            logger.warning(f"Non-admin user {current_user.username} attempted to update menu")
            raise HTTPException(status_code=403, detail="Admin access required for menu updates")
        
        # Check if menu exists
        existing_menu = await menu_repository.get_menu_by_id(menu_id)
        if not existing_menu:
            raise HTTPException(status_code=404, detail=f"Menu with ID {menu_id} not found")
        
        # Check if parent menu exists (if parent_id is being updated)
        if menu_data.parent_id is not None:
            if menu_data.parent_id:  # Not None and not empty
                parent_menu = await menu_repository.get_menu_by_id(menu_data.parent_id)
                if not parent_menu:
                    raise HTTPException(status_code=400, detail=f"Parent menu with ID {menu_data.parent_id} not found")
        
        # Update the menu
        success = await menu_repository.update_menu(menu_id, menu_data)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update menu")
        
        # Get the updated menu
        updated_menu = await menu_repository.get_menu_by_id(menu_id)
        if not updated_menu:
            raise HTTPException(status_code=500, detail="Menu not found after update")
        
        logger.info(f"Successfully updated menu: {updated_menu.name}")
        return updated_menu
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating menu {menu_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update menu: {str(e)}")


@router.delete("/{menu_id}")
async def delete_menu(
    menu_id: str,
    current_user: UserPrincipal = Depends(get_current_user)
):
    """
    Delete (soft delete) a menu item
    
    Args:
        menu_id: ID of the menu to delete
        current_user: Current authenticated user (must have Administration:delete permission)
        
    Returns:
        Success message
    """
    try:
        logger.info(f"Deleting menu ID: {menu_id} by user: {current_user.username}")
        
        # Verify admin permissions
        user_permissions = current_user.permissions
        user_roles = current_user.roles
        
        if "*" not in user_permissions and "admin" not in user_roles:
            logger.warning(f"Non-admin user {current_user.username} attempted to delete menu")
            raise HTTPException(status_code=403, detail="Admin access required for menu deletion")
        
        # Check if menu exists
        existing_menu = await menu_repository.get_menu_by_id(menu_id)
        if not existing_menu:
            raise HTTPException(status_code=404, detail=f"Menu with ID {menu_id} not found")
        
        # Check if menu has children (prevent deletion of parent menus with children)
        child_menus = await menu_repository.get_menus_by_parent_id(menu_id)
        if child_menus:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot delete menu '{existing_menu.name}' because it has {len(child_menus)} child menus. Please delete child menus first."
            )
        
        # Soft delete the menu
        success = await menu_repository.delete_menu(menu_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete menu")
        
        logger.info(f"Successfully deleted menu: {existing_menu.name}")
        return {
            "message": f"Menu '{existing_menu.name}' deleted successfully",
            "menu_id": menu_id,
            "deleted_at": "now"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting menu {menu_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete menu: {str(e)}")


@router.get("/{menu_id}", response_model=MenuInDB)
async def get_menu_by_id(
    menu_id: str,
    current_user: UserPrincipal = Depends(get_current_user)
):
    """
    Get a specific menu by ID
    
    Args:
        menu_id: ID of the menu to retrieve
        current_user: Current authenticated user (must have Administration:read permission)
        
    Returns:
        Menu item details
    """
    try:
        logger.info(f"Getting menu ID: {menu_id} by user: {current_user.username}")
        
        # Verify admin permissions
        user_permissions = current_user.permissions
        user_roles = current_user.roles
        
        if "*" not in user_permissions and "admin" not in user_roles:
            logger.warning(f"Non-admin user {current_user.username} attempted to get menu details")
            raise HTTPException(status_code=403, detail="Admin access required for menu details")
        
        # Get the menu
        menu = await menu_repository.get_menu_by_id(menu_id)
        if not menu:
            raise HTTPException(status_code=404, detail=f"Menu with ID {menu_id} not found")
        
        logger.info(f"Successfully retrieved menu: {menu.name}")
        return menu
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting menu {menu_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve menu: {str(e)}")