def opcion_1():
    print("\nOpción 1 seleccionada: Ver empleados")


def opcion_2():
    print("\nOpción 2 seleccionada: Registrar empleado")


def opcion_3():
    print("\nOpción 3 seleccionada: Ver productos")


def opcion_4():
    print("\nOpción 4 seleccionada: Registrar producto")


def opcion_5():
    print("\nOpción 5 seleccionada: Ver ventas")


def opcion_6():
    print("\nOpción 6 seleccionada: Registrar venta")


def opcion_7():
    print("\nOpción 7 seleccionada: Ver estadísticas")


def opcion_8():
    print("\nOpción 8 seleccionada: Configuración")


def opcion_9():
    import subprocess
    import sys
    import webbrowser
    from pathlib import Path

    app_path = Path(__file__).with_name("app.py")
    server = subprocess.Popen([sys.executable, str(app_path)])
    print("\nGeoRiesgo IA se está iniciando en http://127.0.0.1:5000")
    print("Dejá esta ventana abierta mientras uses la aplicación.")
    webbrowser.open("http://127.0.0.1:5000")
    try:
        server.wait()
    except KeyboardInterrupt:
        server.terminate()
        return True
    return False


menu = {
    1: opcion_1,
    2: opcion_2,
    3: opcion_3,
    4: opcion_4,
    5: opcion_5,
    6: opcion_6,
    7: opcion_7,
    8: opcion_8,
    9: opcion_9,
}


while True:
    print("\n===== MENÚ =====")
    print("1. Ver empleados")
    print("2. Registrar empleado")
    print("3. Ver productos")
    print("4. Registrar producto")
    print("5. Ver ventas")
    print("6. Registrar venta")
    print("7. Ver estadísticas")
    print("8. Configuración")
    print("9. GeoRiesgo IA - Análisis territorial")

    try:
        eleccion = int(input("\nElegí una opción: "))
    except ValueError:
        print("Ingresá un número válido.")
        continue

    if eleccion in menu:
        salir = menu[eleccion]()
        if salir:
            break
    else:
        print("La opción ingresada no existe.")
